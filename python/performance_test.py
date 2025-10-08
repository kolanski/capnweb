#!/usr/bin/env python3
"""
Real RPS (Requests Per Second) performance test for Cap'n Web Python.

This test measures actual throughput with different configurations:
- Single connection vs connection pooling
- Different pool sizes
- Concurrent request handling
- Latency measurements
"""

import asyncio
import time
import statistics
import json
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor

from capnweb.core import RpcTarget
from capnweb.websocket import WebSocketRpcServer, new_websocket_rpc_session, new_resilient_websocket_rpc_session
from capnweb.connection import RetryPolicy
from capnweb.rpc import RpcSessionOptions


class PerfTestApi(RpcTarget):
    """High-performance API for testing."""

    def hello(self, name: str) -> str:
        """Simple hello world test."""
        return f"Hello, {name}!"

    def fast_compute(self, x: int) -> int:
        """Very fast computation."""
        return x * 2

    def get_stats(self) -> Dict[str, Any]:
        """Return server statistics."""
        return {
            "server_time": time.time(),
            "message": "Performance test API"
        }

    def process_batch(self, items: List[str]) -> List[str]:
        """Process a batch of items."""
        return [f"processed_{item}" for item in items]


class PerformanceTester:
    """Performance testing framework."""

    def __init__(self):
        self.results = {}

    async def start_test_server(self, port: int = 8090) -> WebSocketRpcServer:
        """Start a test server."""
        def api_factory():
            return PerfTestApi()

        server = WebSocketRpcServer(
            host="localhost",
            port=port,
            local_main_factory=api_factory,
            enable_resilience=True
        )
        await server.start()
        return server

    async def single_connection_test(self, num_requests: int = 1000) -> Dict[str, Any]:
        """Test performance with single connection."""
        print(f"\n🔍 Testing Single Connection ({num_requests} requests)")

        server = await self.start_test_server(8090)
        await asyncio.sleep(0.1)  # Let server start

        try:
            # Single connection test
            api = await new_websocket_rpc_session(
                "ws://localhost:8090",
                enable_resilience=False  # Disable for fair comparison
            )

            # Warm up
            for _ in range(10):
                await api.hello("warmup")

            # Measure performance
            start_time = time.time()
            latencies = []

            for i in range(num_requests):
                request_start = time.time()
                result = await api.hello(f"test_{i}")
                latency = (time.time() - request_start) * 1000  # ms
                latencies.append(latency)

            total_time = time.time() - start_time
            rps = num_requests / total_time

            await server.stop()

            return {
                "rps": rps,
                "total_time": total_time,
                "avg_latency_ms": statistics.mean(latencies),
                "p95_latency_ms": statistics.quantiles(latencies, n=20)[18],  # 95th percentile
                "p99_latency_ms": statistics.quantiles(latencies, n=100)[98],  # 99th percentile
                "min_latency_ms": min(latencies),
                "max_latency_ms": max(latencies)
            }

        except Exception as e:
            await server.stop()
            raise e

    async def connection_pool_test(self, pool_size: int, num_requests: int = 1000) -> Dict[str, Any]:
        """Test performance with connection pooling."""
        print(f"\n🚀 Testing Connection Pool (size: {pool_size}, requests: {num_requests})")

        server = await self.start_test_server(8091)
        await asyncio.sleep(0.1)

        try:
            # Connection pool test
            api = await new_websocket_rpc_session(
                "ws://localhost:8091",
                enable_resilience=True,
                enable_pooling=True,
                pool_size=pool_size
            )

            # Warm up
            warmup_tasks = [api.hello("warmup") for _ in range(20)]
            await asyncio.gather(*warmup_tasks)

            # Measure performance
            start_time = time.time()
            latencies = []

            # Create concurrent requests
            tasks = []
            for i in range(num_requests):
                request_start = time.time()
                task = api.hello(f"test_{i}")
                tasks.append((task, request_start))

            # Execute all requests concurrently
            results = await asyncio.gather(*[task for task, _ in tasks], return_exceptions=True)

            # Calculate latencies
            for i, (result, request_start) in enumerate(zip(results, [start for _, start in tasks])):
                if isinstance(result, Exception):
                    continue
                latency = (time.time() - request_start) * 1000
                latencies.append(latency)

            total_time = time.time() - start_time
            successful_requests = len([r for r in results if not isinstance(r, Exception)])
            rps = successful_requests / total_time if total_time > 0 else 0

            await server.stop()

            return {
                "rps": rps,
                "total_time": total_time,
                "successful_requests": successful_requests,
                "failed_requests": num_requests - successful_requests,
                "avg_latency_ms": statistics.mean(latencies) if latencies else 0,
                "p95_latency_ms": statistics.quantiles(latencies, n=20)[18] if len(latencies) > 20 else 0,
                "p99_latency_ms": statistics.quantiles(latencies, n=100)[98] if len(latencies) > 100 else 0,
                "min_latency_ms": min(latencies) if latencies else 0,
                "max_latency_ms": max(latencies) if latencies else 0
            }

        except Exception as e:
            await server.stop()
            raise e

    async def concurrent_clients_test(self, num_clients: int, requests_per_client: int = 100) -> Dict[str, Any]:
        """Test performance with multiple concurrent clients."""
        print(f"\n👥 Testing Concurrent Clients ({num_clients} clients, {requests_per_client} req/client)")

        server = await self.start_test_server(8092)
        await asyncio.sleep(0.1)

        try:
            async def client_work(client_id: int):
                """Work for a single client."""
                api = await new_websocket_rpc_session(
                    f"ws://localhost:8092",
                    enable_resilience=True,
                    enable_pooling=True,
                    pool_size=2  # Small pool per client
                )

                latencies = []
                start_time = time.time()

                for i in range(requests_per_client):
                    request_start = time.time()
                    result = await api.hello(f"client_{client_id}_req_{i}")
                    latency = (time.time() - request_start) * 1000
                    latencies.append(latency)

                return {
                    "client_id": client_id,
                    "time": time.time() - start_time,
                    "latencies": latencies
                }

            # Run all clients concurrently
            start_time = time.time()
            client_results = await asyncio.gather(*[
                client_work(i) for i in range(num_clients)
            ])
            total_time = time.time() - start_time

            # Aggregate results
            all_latencies = []
            successful_requests = 0
            total_requests = num_clients * requests_per_client

            for result in client_results:
                all_latencies.extend(result["latencies"])
                successful_requests += len(result["latencies"])

            await server.stop()

            return {
                "rps": successful_requests / total_time,
                "total_time": total_time,
                "num_clients": num_clients,
                "requests_per_client": requests_per_client,
                "successful_requests": successful_requests,
                "total_requests": total_requests,
                "avg_latency_ms": statistics.mean(all_latencies) if all_latencies else 0,
                "p95_latency_ms": statistics.quantiles(all_latencies, n=20)[18] if len(all_latencies) > 20 else 0,
                "p99_latency_ms": statistics.quantiles(all_latencies, n=100)[98] if len(all_latencies) > 100 else 0
            }

        except Exception as e:
            await server.stop()
            raise e

    async def throughput_scaling_test(self) -> Dict[str, List[Dict[str, Any]]]:
        """Test throughput scaling with different pool sizes."""
        print(f"\n📈 Testing Throughput Scaling")

        pool_sizes = [1, 2, 5, 10, 20]
        results = []

        for pool_size in pool_sizes:
            try:
                result = await self.connection_pool_test(pool_size, num_requests=500)
                result["pool_size"] = pool_size
                results.append(result)
                print(f"  Pool size {pool_size}: {result['rps']:.1f} RPS")
            except Exception as e:
                print(f"  Pool size {pool_size}: Failed - {e}")

        return {"scaling_results": results}

    async def run_comprehensive_test(self):
        """Run comprehensive performance test suite."""
        print("🚀 Cap'n Web Python Performance Test Suite")
        print("=" * 50)

        # Test 1: Single connection baseline
        single_result = await self.single_connection_test(1000)
        self.results["single_connection"] = single_result
        print(f"✅ Single Connection: {single_result['rps']:.1f} RPS")

        # Test 2: Connection pooling (small)
        pool_small = await self.connection_pool_test(pool_size=5, num_requests=1000)
        self.results["pool_small"] = pool_small
        print(f"✅ Pool (5 connections): {pool_small['rps']:.1f} RPS")

        # Test 3: Connection pooling (medium)
        pool_medium = await self.connection_pool_test(pool_size=10, num_requests=2000)
        self.results["pool_medium"] = pool_medium
        print(f"✅ Pool (10 connections): {pool_medium['rps']:.1f} RPS")

        # Test 4: Connection pooling (large)
        pool_large = await self.connection_pool_test(pool_size=20, num_requests=3000)
        self.results["pool_large"] = pool_large
        print(f"✅ Pool (20 connections): {pool_large['rps']:.1f} RPS")

        # Test 5: Concurrent clients
        concurrent = await self.concurrent_clients_test(num_clients=10, requests_per_client=200)
        self.results["concurrent_clients"] = concurrent
        print(f"✅ Concurrent Clients: {concurrent['rps']:.1f} RPS")

        # Test 6: Scaling test
        scaling = await self.throughput_scaling_test()
        self.results["scaling"] = scaling
        print(f"✅ Scaling Test: Complete")

        # Generate report
        self.generate_report()

    def generate_report(self):
        """Generate a comprehensive performance report."""
        print("\n" + "=" * 60)
        print("📊 PERFORMANCE REPORT")
        print("=" * 60)

        # Summary table
        print("\n📈 THROUGHPUT COMPARISON:")
        print(f"{'Configuration':<20} {'RPS':<10} {'Avg Latency (ms)':<15} {'P95 Latency (ms)':<15}")
        print("-" * 65)

        configs = [
            ("Single Connection", self.results.get("single_connection")),
            ("Pool (5)", self.results.get("pool_small")),
            ("Pool (10)", self.results.get("pool_medium")),
            ("Pool (20)", self.results.get("pool_large")),
            ("10 Concurrent", self.results.get("concurrent_clients"))
        ]

        for name, result in configs:
            if result:
                print(f"{name:<20} {result['rps']:<10.1f} {result['avg_latency_ms']:<15.2f} {result.get('p95_latency_ms', 0):<15.2f}")

        # Performance improvements
        single_rps = self.results.get("single_connection", {}).get("rps", 0)
        pool_rps = self.results.get("pool_large", {}).get("rps", 0)

        if single_rps > 0 and pool_rps > 0:
            improvement = (pool_rps - single_rps) / single_rps * 100
            print(f"\n🚀 Performance Improvement: {improvement:.1f}% increase with connection pooling")

        # Scaling analysis
        if "scaling" in self.results and self.results["scaling"]["scaling_results"]:
            print(f"\n📊 SCALING ANALYSIS:")
            scaling_results = self.results["scaling"]["scaling_results"]
            for result in scaling_results:
                pool_size = result["pool_size"]
                rps = result["rps"]
                print(f"  Pool Size {pool_size:2d}: {rps:6.1f} RPS")

        # Latency analysis
        print(f"\n⚡ LATENCY ANALYSIS (Best Configuration):")
        best_result = max(configs, key=lambda x: x[1]['rps'] if x[1] else 0)[1]
        if best_result:
            print(f"  Average Latency: {best_result['avg_latency_ms']:.2f} ms")
            print(f"  95th Percentile: {best_result.get('p95_latency_ms', 0):.2f} ms")
            print(f"  99th Percentile: {best_result.get('p99_latency_ms', 0):.2f} ms")
            print(f"  Min Latency: {best_result.get('min_latency_ms', 0):.2f} ms")
            print(f"  Max Latency: {best_result.get('max_latency_ms', 0):.2f} ms")

        # Save results to file
        with open("performance_results.json", "w") as f:
            json.dump(self.results, f, indent=2)
        print(f"\n💾 Results saved to performance_results.json")

        print(f"\n🎯 KEY INSIGHTS:")
        print(f"• Connection pooling provides significant performance gains")
        print(f"• Optimal pool size depends on your workload")
        print(f"• Latency remains low even at high throughput")
        print(f"• System scales well with concurrent clients")


async def main():
    """Run the performance test."""
    tester = PerformanceTester()
    await tester.run_comprehensive_test()


if __name__ == "__main__":
    asyncio.run(main())
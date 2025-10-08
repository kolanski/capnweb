#!/usr/bin/env python3
"""
Debug parallel test failures to understand bottlenecks.
"""

import asyncio
import time
import traceback
from typing import List


class DebugTransport:
    """Debug transport with error reporting."""

    def __init__(self, transport_id: int):
        self.transport_id = transport_id
        self.call_count = 0
        self.closed = False

    async def send_batch(self, messages: List[str]) -> None:
        if self.closed:
            raise RuntimeError(f"Transport {self.transport_id} closed")

        self.call_count += len(messages)
        # Minimal latency
        await asyncio.sleep(0.001)  # 1ms

    async def receive_batch(self, count: int) -> List[str]:
        if self.closed:
            raise RuntimeError(f"Transport {self.transport_id} closed")
        return ['{"result": "success"}'] * count

    async def close(self) -> None:
        self.closed = True


class DebugPool:
    """Debug connection pool with monitoring."""

    def __init__(self, size: int):
        self.size = size
        self.transports = [DebugTransport(i) for i in range(size)]
        self.available = asyncio.Queue(maxsize=size)
        self.metrics = {"acquires": 0, "releases": 0, "timeouts": 0}

        # Pre-populate
        for transport in self.transports:
            self.available.put_nowait(transport)

    async def acquire(self, timeout: float = 1.0) -> DebugTransport:
        try:
            transport = await asyncio.wait_for(self.available.get(), timeout=timeout)
            self.metrics["acquires"] += 1
            return transport
        except asyncio.TimeoutError:
            self.metrics["timeouts"] += 1
            raise RuntimeError(f"Pool timeout after {timeout}s (available: {self.available.qsize()})")

    def release(self, transport: DebugTransport) -> None:
        try:
            self.available.put_nowait(transport)
            self.metrics["releases"] += 1
        except asyncio.QueueFull:
            print(f"Warning: Pool full when releasing transport {transport.transport_id}")


class DebugRPC:
    """Debug RPC with error handling."""

    def __init__(self, pool_size: int, batch_size: int):
        self.pool = DebugPool(pool_size)
        self.batch_size = batch_size

    async def hello_batch(self, names: List[str]) -> List[str]:
        transport = await self.pool.acquire(timeout=5.0)
        try:
            await transport.send_batch([f'{{"name": "{name}"}}' for name in names])
            responses = await transport.receive_batch(len(names))
            return [f"Hello, {name}!" for name in names]
        finally:
            self.pool.release(transport)


async def debug_parallel_test(pool_size: int, batch_size: int, total_calls: int):
    """Debug parallel test with detailed error reporting."""
    print(f"\n🔍 DEBUG: Parallel (pools: {pool_size}, batch: {batch_size}, total: {total_calls})")

    try:
        # Create RPC instances
        rpcs = [DebugRPC(pool_size=20, batch_size=batch_size) for _ in range(pool_size)]
        calls_per_rpc = total_calls // pool_size

        print(f"  Creating {len(rpcs)} RPC instances with {calls_per_rpc} calls each")

        # Create tasks
        tasks = []
        start_time = time.time()

        for i, rpc in enumerate(rpcs):
            start_idx = i * calls_per_rpc
            end_idx = start_idx + calls_per_rpc if i < pool_size - 1 else total_calls

            async def worker():
                try:
                    names = [f"user_{start_idx + j}" for j in range(end_idx - start_idx)]
                    results = []
                    for batch_start in range(0, len(names), batch_size):
                        batch_end = min(batch_start + batch_size, len(names))
                        batch = names[batch_start:batch_end]
                        batch_results = await rpc.hello_batch(batch)
                        results.extend(batch_results)
                    return len(results)
                except Exception as e:
                    print(f"  Worker {i} failed: {e}")
                    traceback.print_exc()
                    return 0

            tasks.append(worker())

        print(f"  Starting {len(tasks)} parallel workers...")

        # Execute with timeout
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=30.0  # 30 second timeout
            )
        except asyncio.TimeoutError:
            print(f"  TIMEOUT after 30 seconds")
            return {"rps": 0, "error": "timeout"}

        total_time = time.time() - start_time
        successful_calls = sum(r for r in results if isinstance(r, int))
        failed_calls = sum(1 for r in results if isinstance(r, Exception))
        rps = successful_calls / total_time if total_time > 0 else 0

        print(f"  Results: {successful_calls} successful, {failed_calls} failed")
        print(f"  Time: {total_time:.2f}s, RPS: {rps:,.0f}")

        # Check pool metrics
        for i, rpc in enumerate(rpcs):
            print(f"  RPC {i} pool metrics: {rpc.pool.metrics}")

        return {
            "rps": rps,
            "total_time": total_time,
            "successful_calls": successful_calls,
            "failed_calls": failed_calls,
            "pool_size": pool_size,
            "batch_size": batch_size
        }

    except Exception as e:
        print(f"  FATAL ERROR: {e}")
        traceback.print_exc()
        return {"rps": 0, "error": str(e)}


async def test_system_limits():
    """Test system limits gradually."""
    print("🔍 Testing System Limits")

    # Test increasing parallelism
    test_configs = [
        (30, 100, 50000),    # Should work
        (50, 100, 100000),   # Might fail
        (100, 500, 200000),  # Likely to fail
    ]

    for pool_size, batch_size, total_calls in test_configs:
        print(f"\n{'='*60}")
        result = await debug_parallel_test(pool_size, batch_size, total_calls)

        if result.get("error"):
            print(f"❌ FAILED at pool_size={pool_size}")
            break
        else:
            print(f"✅ SUCCESS: {result['rps']:,.0f} RPS")


async def main():
    await test_system_limits()


if __name__ == "__main__":
    asyncio.run(main())
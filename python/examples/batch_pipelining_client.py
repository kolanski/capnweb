#!/usr/bin/env python3
"""
Client demonstrating HTTP batch pipelining in Python.

This is the Python equivalent of examples/batch-pipelining/client.mjs

Demonstrates:
- Batching + pipelining: multiple dependent calls, one round trip
- Non-batched sequential calls: multiple round trips

Usage (separate terminal from server):
    python examples/batch_pipelining_client.py
"""

import asyncio
import os
import sys
import time
import random

# Add parent directory to path to import capnweb
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from capnweb import new_http_batch_rpc_session


# Configuration
RPC_URL = os.environ.get('RPC_URL', 'http://localhost:3000/rpc')
SIMULATED_RTT_MS = float(os.environ.get('SIMULATED_RTT_MS', '120'))  # per-direction
SIMULATED_RTT_JITTER_MS = float(os.environ.get('SIMULATED_RTT_JITTER_MS', '40'))

# Track fetch count
fetch_count = 0


def jittered_delay() -> float:
    """Calculate a jittered delay in seconds."""
    jitter = random.random() * SIMULATED_RTT_JITTER_MS if SIMULATED_RTT_JITTER_MS else 0
    return (SIMULATED_RTT_MS + jitter) / 1000


# Monkey-patch aiohttp to add simulated latency and count requests
original_request = None

async def patched_request(self, method, url, **kwargs):
    """Patched request method that adds latency simulation."""
    global fetch_count
    
    if url.startswith(RPC_URL) and method.upper() == 'POST':
        fetch_count += 1
        # Simulate uplink latency
        await asyncio.sleep(jittered_delay())
        response = await original_request(self, method, url, **kwargs)
        # Simulate downlink latency
        await asyncio.sleep(jittered_delay())
        return response
    
    return await original_request(self, method, url, **kwargs)


def patch_aiohttp():
    """Apply the monkey patch to aiohttp."""
    global original_request
    import aiohttp
    if original_request is None:
        original_request = aiohttp.ClientSession._request
        aiohttp.ClientSession._request = patched_request


async def run_pipelined():
    """Run pipelined batch requests (all in one HTTP POST)."""
    global fetch_count
    fetch_count = 0
    
    t0 = time.time()
    
    api = await new_http_batch_rpc_session(RPC_URL)
    
    # These calls are pipelined - they all go in ONE HTTP request
    user = api.authenticate('cookie-123')
    profile = api.get_user_profile(user.id)
    notifications = api.get_notifications(user.id)
    
    # Wait for all results
    u, p, n = await asyncio.gather(user, profile, notifications)
    
    t1 = time.time()
    return {
        'u': u,
        'p': p,
        'n': n,
        'ms': (t1 - t0) * 1000,
        'posts': fetch_count
    }


async def run_sequential():
    """Run sequential requests (one HTTP POST per call)."""
    global fetch_count
    fetch_count = 0
    
    t0 = time.time()
    
    # Sequential calls - each one waits for the previous
    api1 = await new_http_batch_rpc_session(RPC_URL)
    u = await api1.authenticate('cookie-123')
    
    api2 = await new_http_batch_rpc_session(RPC_URL)
    p = await api2.get_user_profile(u['id'])
    
    api3 = await new_http_batch_rpc_session(RPC_URL)
    n = await api3.get_notifications(u['id'])
    
    t1 = time.time()
    return {
        'u': u,
        'p': p,
        'n': n,
        'ms': (t1 - t0) * 1000,
        'posts': fetch_count
    }


async def main():
    """Main demonstration."""
    patch_aiohttp()
    
    print(f'Simulated network RTT (each direction): ~{SIMULATED_RTT_MS}ms ±{SIMULATED_RTT_JITTER_MS}ms')
    
    print('\n--- Running pipelined (batched, single round trip) ---')
    pipelined = await run_pipelined()
    print(f'HTTP POSTs: {pipelined["posts"]}')
    print(f'Time: {pipelined["ms"]:.2f} ms')
    print(f'Authenticated user: {pipelined["u"]}')
    print(f'Profile: {pipelined["p"]}')
    print(f'Notifications: {pipelined["n"]}')
    
    print('\n--- Running sequential (non-batched, multiple round trips) ---')
    sequential = await run_sequential()
    print(f'HTTP POSTs: {sequential["posts"]}')
    print(f'Time: {sequential["ms"]:.2f} ms')
    print(f'Authenticated user: {sequential["u"]}')
    print(f'Profile: {sequential["p"]}')
    print(f'Notifications: {sequential["n"]}')
    
    print('\nSummary:')
    print(f'Pipelined: {pipelined["posts"]} POST, {pipelined["ms"]:.2f} ms')
    print(f'Sequential: {sequential["posts"]} POSTs, {sequential["ms"]:.2f} ms')


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception as e:
        import traceback
        traceback.print_exc()
        sys.exit(1)

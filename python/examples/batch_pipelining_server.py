#!/usr/bin/env python3
"""
Minimal Python HTTP server exposing an RPC endpoint over HTTP batching.

This is the Python equivalent of examples/batch-pipelining/server-node.mjs

Usage:
    1) Install dependencies: pip install aiohttp
    2) Start server: python examples/batch_pipelining_server.py
    3) Run client: python examples/batch_pipelining_client.py
"""

import asyncio
import os
import sys
from aiohttp import web

# Add parent directory to path to import capnweb
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from capnweb import RpcTarget, new_http_batch_rpc_response


# Simple in-memory data
USERS = {
    'cookie-123': {'id': 'u_1', 'name': 'Ada Lovelace'},
    'cookie-456': {'id': 'u_2', 'name': 'Alan Turing'},
}

PROFILES = {
    'u_1': {'id': 'u_1', 'bio': 'Mathematician & first programmer'},
    'u_2': {'id': 'u_2', 'bio': 'Mathematician & computer science pioneer'},
}

NOTIFICATIONS = {
    'u_1': ['Welcome to capnweb!', 'You have 2 new followers'],
    'u_2': ['New feature: pipelining!', 'Security tips for your account'],
}


# Define the server-side API by extending RpcTarget
class Api(RpcTarget):
    """Server API implementation."""
    
    async def authenticate(self, session_token: str) -> dict:
        """Simulate authentication from a session cookie/token."""
        delay = float(os.environ.get('DELAY_AUTH_MS', '80')) / 1000
        await asyncio.sleep(delay)
        
        user = USERS.get(session_token)
        if not user:
            raise Exception('Invalid session')
        return user  # {'id': ..., 'name': ...}
    
    async def get_user_profile(self, user_id: str) -> dict:
        """Get user profile by ID."""
        delay = float(os.environ.get('DELAY_PROFILE_MS', '120')) / 1000
        await asyncio.sleep(delay)
        
        profile = PROFILES.get(user_id)
        if not profile:
            raise Exception('No such user')
        return profile  # {'id': ..., 'bio': ...}
    
    async def get_notifications(self, user_id: str) -> list:
        """Get notifications for a user."""
        delay = float(os.environ.get('DELAY_NOTIFS_MS', '120')) / 1000
        await asyncio.sleep(delay)
        
        return NOTIFICATIONS.get(user_id, [])


async def handle_rpc(request):
    """Handle POST /rpc requests with batch RPC processing."""
    if request.method != 'POST':
        return web.Response(text='Not Found', status=404)
    
    try:
        body = await request.text()
        response_body = await new_http_batch_rpc_response(body, Api())
        return web.Response(text=response_body)
    except Exception as e:
        import traceback
        return web.Response(text=traceback.format_exc(), status=500)


async def main():
    """Start the HTTP server."""
    port = int(os.environ.get('PORT', '3000'))
    
    app = web.Application()
    app.router.add_post('/rpc', handle_rpc)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, 'localhost', port)
    await site.start()
    
    print(f'RPC server listening on http://localhost:{port}/rpc')
    
    # Keep the server running
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        print('\nShutting down server...')
        await runner.cleanup()


if __name__ == '__main__':
    asyncio.run(main())

import asyncio
import itertools
from aiohttp import web, ClientSession

# --- Configuration ---
# The address where the load balancer will listen.
LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 8000

# A list of the backend MCP server addresses.
# The load balancer will distribute requests among these servers.
BACKEND_SERVERS = [
    "http://127.0.0.1:8001",
    "http://127.0.0.1:8002",
    "http://127.0.0.1:8003",
]

# --- Load Balancer Logic ---

# Create a cyclical iterator to rotate through the backend servers.
server_pool = itertools.cycle(BACKEND_SERVERS)

async def handle_request(request: web.Request) -> web.StreamResponse:
    """
    Handles an incoming request by forwarding it to a backend server.
    """
    backend_server_url = next(server_pool)
    target_url = f"{backend_server_url}{request.path_qs}"

    print(f"Forwarding request for {request.path} to {target_url}")

    try:
        async with ClientSession() as session:
            # Forward the request to the chosen backend server
            async with session.request(
                request.method,
                target_url,
                headers=request.headers,
                data=await request.read()
            ) as backend_response:
                # Prepare the response to be sent back to the client
                response = web.StreamResponse(
                    status=backend_response.status,
                    headers=backend_response.headers
                )
                await response.prepare(request)

                # Stream the content from the backend response to the client
                async for chunk in backend_response.content.iter_any():
                    await response.write(chunk)

                await response.write_eof()
                return response

    except Exception as e:
        print(f"Error forwarding request to {target_url}: {e}")
        return web.Response(status=502, text="Bad Gateway")

# --- Application Setup ---

app = web.Application()
# Add a route that captures all paths and methods
app.router.add_route("*", "/{tail:.*}", handle_request)

if __name__ == "__main__":
    print(f"Load balancer starting on http://{LISTEN_HOST}:{LISTEN_PORT}")
    print(f"Distributing requests to: {', '.join(BACKEND_SERVERS)}")
    web.run_app(app, host=LISTEN_HOST, port=LISTEN_PORT)

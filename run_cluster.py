import subprocess
import time
import os
import atexit

# --- Configuration ---
LOG_DIR = "logs"
SERVER_PORTS = [8001, 8002, 8003]
LOAD_BALANCER_PORT = 8000

# --- Process Management ---
processes = []

def cleanup_processes():
    """Ensure all child processes are terminated on exit."""
    print("Shutting down all processes...")
    for p in processes:
        p.terminate()
    print("Cleanup complete.")

atexit.register(cleanup_processes)

def main():
    """
    Starts the MCP server cluster and the load balancer.
    """
    # Create logs directory if it doesn't exist
    os.makedirs(LOG_DIR, exist_ok=True)

    # Clear old logs
    for f in os.listdir(LOG_DIR):
        os.remove(os.path.join(LOG_DIR, f))

    # Install dependencies
    print("Installing dependencies...")
    try:
        subprocess.run(
            ["pip", "install", "-r", "requirements.txt"],
            check=True,
            capture_output=True,
            text=True
        )
    except subprocess.CalledProcessError as e:
        print("Failed to install dependencies:")
        print(e.stdout)
        print(e.stderr)
        return

    # Start backend MCP servers
    print("Starting backend MCP servers...")
    for port in SERVER_PORTS:
        log_file = open(os.path.join(LOG_DIR, f"server_{port}.log"), "w")
        p = subprocess.Popen(
            ["python", "mcp_server.py", "--port", str(port)],
            stdout=log_file,
            stderr=log_file
        )
        processes.append(p)
        print(f"  - Started server on port {port} (PID: {p.pid})")

    # Wait for servers to start
    time.sleep(3)

    # Start the load balancer
    print("Starting load balancer...")
    lb_log_file = open(os.path.join(LOG_DIR, "load_balancer.log"), "w")
    lb_p = subprocess.Popen(
        ["python", "load_balancer.py"],
        stdout=lb_log_file,
        stderr=lb_log_file
    )
    processes.append(lb_p)
    print(f"  - Started load balancer on port {LOAD_BALANCER_PORT} (PID: {lb_p.pid})")

    print("\nCluster is running. Press Ctrl+C to stop.")

    try:
        # Keep the main script alive while the cluster is running
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nCtrl+C detected.")

if __name__ == "__main__":
    main()

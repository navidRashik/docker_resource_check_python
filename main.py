import os
import docker
import asyncio
import logging
import math

# Configure logging to include timestamps and log level.
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


async def heavy_computation():
    """
    Perform a heavy computation that's both memory- and CPU-intensive:
    
    - Allocates a 1500x1500 matrix.
    - Iterates over each element and computes the square root (adding 1 to vary the work).
    """
    size = 1500
    # Allocate a 2D matrix, which uses a significant amount of memory.
    matrix = [[(i * j) % 1000 for j in range(size)] for i in range(size)]
    total = 0.0
    for row in matrix:
        await asyncio.sleep(0.001)  # Simulate some network io call.
        for value in row:
            total += math.sqrt(value + 1)
    logging.info(f"Heavy computation result: {total:.2f}")
    return total

async def async_heavy_computation():
    """
    Wrap the heavy computation into an asynchronous callable.
    This will offload the CPU-bound heavy task to a background thread.
    """
    return await asyncio.to_thread(heavy_computation)

async def heavy_computation_background():
    """
    Schedule the heavy computation as a background task using asyncio.create_task.
    
    Every second, a new heavy computation task is created.
    The task's result is logged when it completes via a callback.
    """
    while True:
        # Create the heavy task without awaiting its completion.
        # task = asyncio.create_task(async_heavy_computation())
        task = asyncio.create_task(heavy_computation())
        # Use a callback to log the result when the task is done.
        task.add_done_callback(lambda t: logging.info(f"Heavy computation result: {t.result():.2f}"))
        # Immediately move on and schedule the next task.
        await asyncio.sleep(.1)

def get_cpu_limit():
    """
    Determine the container's CPU limit by checking cgroup files.
    
    For cgroup v1, read:
       /sys/fs/cgroup/cpu/cpu.cfs_quota_us and /sys/fs/cgroup/cpu/cpu.cfs_period_us
    For cgroup v2, read:
       /sys/fs/cgroup/cpu.max  (which typically contains "quota period" or "max period")
    
    Returns the fraction of a full CPU allocated, e.g. 0.25 if the container is limited to 25% of one CPU.
    If no limit is detected, returns 1.0.
    """
    # Try cgroup v1 first.
    try:
        with open("/sys/fs/cgroup/cpu/cpu.cfs_quota_us", "r") as f:
            quota = int(f.read().strip())
        with open("/sys/fs/cgroup/cpu/cpu.cfs_period_us", "r") as f:
            period = int(f.read().strip())
        if quota > 0:
            return quota / period
    except Exception as e:
        logging.debug(f"Cgroup v1 read failed: {e}")
    
    # If not found, try cgroup v2.
    try:
        with open("/sys/fs/cgroup/cpu.max", "r") as f:
            # The file usually looks like: "25000 100000" or "max 100000"
            parts = f.read().strip().split()
            if parts[0] != "max":
                quota = int(parts[0])
                period = int(parts[1])
                if quota > 0:
                    return quota / period
    except Exception as e:
        logging.debug(f"Cgroup v2 read failed: {e}")
    
    # If both fail, assume no limit.
    return 1.0

def calculate_cpu_percent(stats):
    """
    Calculate the raw CPU usage percentage (relative to one full CPU)
    using the formula:
    
      raw_cpu_percent = (Δcontainer_total_usage / Δsystem_cpu_usage) × number_of_cpus × 100
    
    Where:
       Δcontainer_total_usage = cpu_stats.cpu_usage.total_usage − precpu_stats.cpu_usage.total_usage
       Δsystem_cpu_usage    = cpu_stats.system_cpu_usage − precpu_stats.system_cpu_usage
    """
    try:
        cpu_stats = stats.get("cpu_stats", {})
        precpu_stats = stats.get("precpu_stats", {})
        
        if not precpu_stats or not precpu_stats.get("cpu_usage", {}).get("total_usage"):
            return 0.0

        total_usage_current = cpu_stats["cpu_usage"]["total_usage"]
        total_usage_prev = precpu_stats["cpu_usage"]["total_usage"]
        delta_container = total_usage_current - total_usage_prev
        
        system_cpu_current = cpu_stats.get("system_cpu_usage", 0)
        system_cpu_prev = precpu_stats.get("system_cpu_usage", 0)
        delta_system = system_cpu_current - system_cpu_prev

        if delta_system > 0 and delta_container > 0:
            online_cpus = cpu_stats.get("online_cpus") or len(cpu_stats["cpu_usage"].get("percpu_usage", [])) or 1
            return (delta_container / delta_system) * online_cpus * 100.0
    except Exception as e:
        logging.error(f"Error calculating CPU percent: {e}")
    return 0.0

async def monitor_own_container():
    """
    Monitor the resource usage (CPU and memory) for this container.
    
    The container is identified via the HOSTNAME environment variable.
    Docker stats are fetched every second. The function calculates the raw CPU usage
    using the formula:
    
       CPU% = (Δcontainer_total_usage/Δsystem_cpu_usage) × number_of_cpus × 100
    
    Then, the raw value is scaled using the container's CPU limit (detected from cgroup files)
    so that if the container (e.g., limited to 0.25 CPU) is fully utilized, the effective CPU usage
    is reported as 100%.
    """
    client = docker.from_env()
    container_id = os.environ.get("HOSTNAME")  # Typically, inside a container HOSTNAME is its ID.
    
    if not container_id:
        logging.error("HOSTNAME environment variable not found. Cannot determine container ID.")
        return

    try:
        container = client.containers.get(container_id)
    except Exception as e:
        logging.error(f"Error retrieving container '{container_id}': {e}")
        return

    # Determine the container's CPU limit.
    cpu_limit = get_cpu_limit()
    logging.info(f"Detected container CPU limit: {cpu_limit:.2f}")

    while True:
        try:
            # Get a snapshot of the container stats.
            stats = await asyncio.to_thread(container.stats, stream=False)
            if isinstance(stats, list):
                stats = stats[0]  # Use the first snapshot if a list is returned.
            
            cpu_usage = stats["cpu_stats"]["cpu_usage"]["total_usage"]
            mem_usage_bytes = stats["memory_stats"]["usage"]
            mem_limit_bytes = stats["memory_stats"]["limit"]

            mem_usage_mb = mem_usage_bytes / (1024 ** 2)
            mem_limit_mb = mem_limit_bytes / (1024 ** 2)

            raw_cpu_percent = calculate_cpu_percent(stats)
            # Scale raw usage by the detected CPU limit.
            effective_cpu_percent = raw_cpu_percent / cpu_limit if cpu_limit > 0 else raw_cpu_percent

            logging.info(
                f"[Self] CPU usage: {cpu_usage}, Raw CPU percent: {raw_cpu_percent:.2f}%, "
                f"Effective CPU percent: {effective_cpu_percent:.2f}%, "
                f"Memory: {mem_usage_mb:.2f} MB / {mem_limit_mb:.2f} MB"
            )
        except Exception as e:
            logging.error(f"Error fetching container stats: {e}")
        
        await asyncio.sleep(1)

async def main():
    # Run both the container monitoring and heavy computation background tasks concurrently.
    await asyncio.gather(
        monitor_own_container(),
        heavy_computation_background()
    )

if __name__ == '__main__':
    asyncio.run(main())

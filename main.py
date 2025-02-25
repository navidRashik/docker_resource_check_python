import os
import docker
import asyncio
import logging
import math

# Configure logging to include timestamps and log level.
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

def calculate_cpu_percent(stats):
    """
    Calculate the container's CPU usage percentage using the difference
    between the current and previous CPU stats.
    
    Uses:
      cpu_delta = current_total_usage - previous_total_usage
      system_delta = current_system_usage - previous_system_usage
      CPU% = (cpu_delta / system_delta) * number_of_cpus * 100
    """
    try:
        cpu_stats = stats.get("cpu_stats", {})
        precpu_stats = stats.get("precpu_stats", {})

        # If previous stats are not yet available, skip calculation.
        if not precpu_stats or not precpu_stats.get("cpu_usage", {}).get("total_usage"):
            return 0.0

        cpu_delta = cpu_stats["cpu_usage"]["total_usage"] - precpu_stats["cpu_usage"]["total_usage"]
        system_delta = cpu_stats.get("system_cpu_usage", 0) - precpu_stats.get("system_cpu_usage", 0)

        if system_delta > 0 and cpu_delta > 0:
            num_cpus = cpu_stats.get("online_cpus") or len(cpu_stats["cpu_usage"].get("percpu_usage", [])) or 1
            return (cpu_delta / system_delta) * num_cpus * 100.0
    except Exception as e:
        logging.error(f"Error calculating CPU percent: {e}")
    return 0.0

def heavy_computation():
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
        for value in row:
            total += math.sqrt(value + 1)
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
        task = asyncio.create_task(async_heavy_computation())
        # Use a callback to log the result when the task is done.
        task.add_done_callback(lambda t: logging.info(f"Heavy computation result: {t.result():.2f}"))
        # Immediately move on and schedule the next task.
        await asyncio.sleep(.01)

async def monitor_own_container():
    """
    Monitor the resource usage (CPU and memory) for this container only.
    
    The container is identified via the HOSTNAME environment variable.
    Docker stats are fetched once per second.
    """
    client = docker.from_env()
    container_id = os.environ.get("HOSTNAME")  # Typically, within a container, HOSTNAME is set to the container ID.
    
    if not container_id:
        logging.error("HOSTNAME environment variable not found. Cannot determine container ID.")
        return

    try:
        container = client.containers.get(container_id)
    except Exception as e:
        logging.error(f"Error retrieving container '{container_id}': {e}")
        return

    while True:
        try:
            # Get a snapshot of the container stats.
            stats = await asyncio.to_thread(container.stats, stream=False)
            logging.info(stats)
            cpu_usage = stats["cpu_stats"]["cpu_usage"]["total_usage"]
            mem_usage_bytes = stats["memory_stats"]["usage"]
            mem_limit_bytes = stats["memory_stats"]["limit"]

            # Convert bytes to megabytes.
            mem_usage_mb = mem_usage_bytes / (1024 ** 2)
            mem_limit_mb = mem_limit_bytes / (1024 ** 2)
            cpu_percent = calculate_cpu_percent(stats)

            logging.info(
                f"[Self] CPU usage: {cpu_usage}, CPU percent: {cpu_percent:.2f}%, "
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

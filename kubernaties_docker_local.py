import os
import time
import asyncio
import logging,math

# Configure basic logging.
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")


async def heavy_computation():
    """
    Perform a heavy computation that's both memory- and CPU-intensive:
    
    - Allocates a 1500x1500 matrix.
    - Iterates over each element and computes the square root (adding 1 to vary the work).
    """
    size = 250
    # Allocate a 2D matrix, which uses a significant amount of memory.
    matrix = [[(i * j) % 1000 for j in range(size)] for i in range(size)]
    total = 0.0
    for row in matrix:
        await asyncio.sleep(0.01)  # Simulate some network io call.
        for value in row:
            total += math.sqrt(value + 1)
    # logging.info(f"Heavy computation result: {total:.2f}")
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
        # task.add_done_callback(lambda t: logging.info(f"Heavy computation result: {t.result():.2f}"))
        # Immediately move on and schedule the next task.
        await asyncio.sleep(.01)

def get_cpu_limit():
    """
    Determine the container's CPU limit.
    
    - First, try cgroup v1 using:
         /sys/fs/cgroup/cpu/cpu.cfs_quota_us and /sys/fs/cgroup/cpu/cpu.cfs_period_us
    - If unavailable, try cgroup v2 from:
         /sys/fs/cgroup/cpu.max
    
    Returns the fraction of a full CPU allocated (for example, 0.25 if the container is limited to 25% of one CPU).
    If no limit is found, returns 1.0.
    """
    # Try cgroup v1.
    cgroup_v1_quota = "/sys/fs/cgroup/cpu/cpu.cfs_quota_us"
    cgroup_v1_period = "/sys/fs/cgroup/cpu/cpu.cfs_period_us"
    if os.path.exists(cgroup_v1_quota) and os.path.exists(cgroup_v1_period):
        try:
            with open(cgroup_v1_quota, "r") as f:
                quota = int(f.read().strip())
            with open(cgroup_v1_period, "r") as f:
                period = int(f.read().strip())
            if quota == -1 or quota <= 0:
                return 1.0
            return quota / period
        except Exception as e:
            logging.error(f"Error reading CPU limit (cgroup v1): {e}")

    # Try cgroup v2.
    cgroup_v2 = "/sys/fs/cgroup/cpu.max"
    if os.path.exists(cgroup_v2):
        try:
            with open(cgroup_v2, "r") as f:
                content = f.read().strip()
                parts = content.split()
                if parts[0] == "max":
                    return 1.0
                else:
                    quota = int(parts[0])
                    period = int(parts[1])
                    if quota <= 0:
                        return 1.0
                    return quota / period
        except Exception as e:
            logging.error(f"Error reading CPU limit (cgroup v2): {e}")
    
    return 1.0


def read_cpu_usage():
    """
    Read the cumulative CPU usage in nanoseconds.
    
    First, try the common cgroup v1 paths.
    If not available, try cgroup v2—by reading /sys/fs/cgroup/cpu.stat and parsing the "usage_usec" value,
    then converting microseconds to nanoseconds.
    """
    # Try cgroup v1 paths.
    paths = [
        "/sys/fs/cgroup/cpu,cpuacct/cpuacct.usage",
        "/sys/fs/cgroup/cpuacct/cpuacct.usage"
    ]
    for path in paths:
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    return int(f.read().strip())
            except Exception as e:
                logging.error(f"Error reading CPU usage from {path}: {e}")
    
    # Try cgroup v2.
    alt_path = "/sys/fs/cgroup/cpu.stat"
    if os.path.exists(alt_path):
        try:
            with open(alt_path, "r") as f:
                lines = f.read().strip().splitlines()
                usage_usec = None
                for line in lines:
                    if line.startswith("usage_usec"):
                        parts = line.split()
                        usage_usec = int(parts[1])
                        break
                if usage_usec is not None:
                    return usage_usec * 1000  # Convert microseconds to nanoseconds.
        except Exception as e:
            logging.error(f"Error reading CPU usage from {alt_path}: {e}")
    return 0


def read_memory_usage():
    """
    Read the container's memory usage in bytes.
    
    Try the cgroup v1 file first and if not available, fallback to the cgroup v2 file.
    """
    paths = [
        "/sys/fs/cgroup/memory/memory.usage_in_bytes",  # cgroup v1
        "/sys/fs/cgroup/memory.current"                  # cgroup v2
    ]
    for path in paths:
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    return int(f.read().strip())
            except Exception as e:
                logging.error(f"Error reading memory usage from {path}: {e}")
    return 0

def get_total_memory_in_bytes():
    # with open('/sys/fs/cgroup/memory/memory', 'r') as f:
    #     print(f.read())

    paths = [
        "/sys/fs/cgroup/memory/memory.max",  # cgroup v1
        "/sys/fs/cgroup/memory.max"                  # cgroup v2
    ]
    for path in paths:
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    return int(f.read().strip())
            except Exception as e:
                logging.error(f"Error reading memory usage from {path}: {e}")
    return 1

async def monitor_resources():
    """
    Monitor the container's CPU and memory usage by reading directly from cgroup files.
    
    For CPU:
      - It takes two successive readings (one second apart) from read_cpu_usage(),
      - Computes the difference, and converts that to a "raw" CPU usage percent as if the container had a full CPU.
      - Then scales that raw percentage by the container's CPU limit so that, for example,
        25% raw usage in a container limited to 0.25 CPU is reported as 100% effective usage.
    
    For memory:
      - It reads the memory usage in bytes and converts it to megabytes.
    """
    cpu_limit = get_cpu_limit()
    logging.info(f"Detected container CPU limit: {cpu_limit:.2f} CPUs")
    
    prev_cpu = read_cpu_usage()
    prev_time = time.monotonic()

    while True:
        await asyncio.sleep(1)  # 1-second monitoring interval.
        current_cpu = read_cpu_usage()
        current_time = time.monotonic()

        delta_cpu = current_cpu - prev_cpu       # in nanoseconds.
        delta_time = current_time - prev_time      # in seconds.

        # Convert delta CPU usage (nanoseconds) into seconds:
        cpu_time_used = delta_cpu / 1e9
        # Compute raw fraction of one CPU used during this interval.
        raw_cpu_fraction = cpu_time_used / delta_time
        raw_cpu_percent = raw_cpu_fraction * 100.0

        # Scale raw CPU percent by the container's limit:
        effective_cpu_percent = (raw_cpu_percent / cpu_limit) if cpu_limit > 0 else raw_cpu_percent

        mem_usage = read_memory_usage()
        mem_usage_mb = mem_usage / (1024 ** 2)
        total_memory_in_bytes = get_total_memory_in_bytes()
        total_memory_in_mb = total_memory_in_bytes / (1024 ** 2)
        print(f"Total memory: {total_memory_in_mb} bytes")
        memory_usage_percentage = (mem_usage_mb / total_memory_in_mb) * 100
        print(f"Memory usage percentage: {memory_usage_percentage:.2f}%")

        logging.info(f"Raw CPU percent: {raw_cpu_percent:.2f}%, "
                     f"Effective CPU percent: {effective_cpu_percent:.2f}%, "
                     f"Memory usage: {mem_usage_mb:.2f} MB"
                     f"Memory usage percentage: {memory_usage_percentage:.2f}%"
                     )

        prev_cpu = current_cpu
        prev_time = current_time


async def main():
    # await monitor_resources()
    await asyncio.gather(
        monitor_resources(),
        heavy_computation_background()
    )


if __name__ == "__main__":
    asyncio.run(main())

# Use a slim version of Python 3.10 as the base image.
FROM python:3.10-slim

# Set the working directory inside the container.
WORKDIR /app

# Copy the requirements file first and install dependencies.
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy the application code.
COPY main.py .

# Run the Python application.
CMD ["python", "main.py"]

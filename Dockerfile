# Dockerfile for Synthetic Trader Bot
# Production-ready containerized deployment

# Use Python 3.12 slim as base image
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ ./src/

# Change ownership to non-root user
RUN chown -R app:app /app

# Switch to non-root user
USER app

# Set Python path
ENV PYTHONPATH=/app/src

# Entrypoint
CMD ["python", "src/main.py"]
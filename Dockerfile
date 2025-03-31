# Builder stage
FROM python:3.11-slim AS builder

# Set environment variables
ENV LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1

# Set up the working directory
WORKDIR /eeb

# Install dependencies (only requirements.txt)
COPY requirements.txt .

# Create and activate a virtual environment directly in /venv/
RUN python -m venv /venv && \
    /venv/bin/pip install --upgrade pip && \
    /venv/bin/pip install --no-cache-dir -r requirements.txt

# Remove unnecessary files to reduce size
RUN find /venv -type f -name '*.py[co]' -delete && \
    find /venv -type d -name '__pycache__' -exec rm -rf {} +

# Production image
FROM python:3.11-slim-bullseye

# Set up the working directory
WORKDIR /app

# Copy over the application code
COPY . ./

# Copy the virtual environment from the builder stage
COPY --from=builder /venv /venv

# Set environment variables for the final stage
ENV PYTHONUNBUFFERED=1 \
    PATH="/venv/bin:$PATH"

# Expose port 8000 to the outside world
EXPOSE 8000

# Define a volume for persistent data
VOLUME /data

# Set the entry point and command to run the application using Gunicorn
ENTRYPOINT ["python"]
CMD ["-m", "gunicorn", "-b", "0.0.0.0:8000", "--worker-class=gevent", "--worker-connections=50", "--workers=3", "--graceful-timeout=900", "--timeout=900", "config.wsgi:application"]

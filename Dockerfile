# Builder stage
FROM python:3.10.10 AS builder

# Set environment variables
ENV LANG=C.UTF-8
ENV PYTHONUNBUFFERED=1

# Set up the working directory
WORKDIR /eeb

# Create and activate a virtual environment
RUN python -m venv /eeb/venv

# Upgrade pip to the latest version compatible with Python 3.10
RUN /eeb/venv/bin/pip install --upgrade pip

# Install dependencies (only requirements.txt)
COPY requirements.txt .
RUN /eeb/venv/bin/pip install --no-cache-dir -r requirements.txt

# Production image
FROM python:3.10.10

# Set up the working directory
WORKDIR /app

# Copy over the application code
COPY . ./

# Copy the virtual environment from the builder stage
COPY --from=builder /eeb/venv /venv

# Set environment variables for the final stage
ENV PYTHONUNBUFFERED=1
ENV PATH="/venv/bin:$PATH"

# Expose port 8000 to the outside world
EXPOSE 8000

# Define a volume for persistent data
VOLUME /data

# Set the entry point and command to run the application using Gunicorn
ENTRYPOINT ["python"]
CMD ["-m", "gunicorn", "-b", "0.0.0.0:8000", "--worker-class=gevent", "--worker-connections=50", "--workers=3", "--graceful-timeout=900", "--timeout=900", "config.wsgi:application"]

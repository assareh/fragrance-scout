# First stage: Build dependencies
FROM python:3.11-slim AS builder

WORKDIR /app

# Install dependencies to /install directory
COPY requirements.txt .
RUN pip install --disable-pip-version-check --no-cache-dir --target=/install -r requirements.txt

# Second stage: Runtime image using distroless
FROM gcr.io/distroless/python3-debian12

WORKDIR /app

# Accept build arguments for version tracking
ARG BUILD_COMMIT_ARG=unknown
ARG BUILD_BRANCH_ARG=unknown
ARG BUILD_TIMESTAMP_ARG=unknown

# Set environment variables from build args
ENV BUILD_COMMIT=${BUILD_COMMIT_ARG}
ENV BUILD_BRANCH=${BUILD_BRANCH_ARG}
ENV BUILD_TIMESTAMP=${BUILD_TIMESTAMP_ARG}

# Set default environment variables (will be overridden by Cloud Run)
ENV PORT=8080
ENV USE_GEMINI=true
ENV PYTHONPATH="/app/install"

# Copy installed dependencies from builder
COPY --from=builder /install /app/install

# Copy application code
COPY fragrance_scout.py .

# Run the application
CMD ["fragrance_scout.py"]

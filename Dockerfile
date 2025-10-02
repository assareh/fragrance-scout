FROM python:3.11-slim

WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY fragrance_scout.py .

# Set environment variables (will be overridden by Cloud Run)
ENV PORT=8080
ENV USE_GEMINI=true

# Run the application
CMD ["python", "fragrance_scout.py"]

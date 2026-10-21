FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* && git config --global --add safe.directory '*'
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY drift ./drift
COPY dashboard ./dashboard
COPY desired ./desired
EXPOSE 5000
CMD ["python", "-m", "dashboard.server"]

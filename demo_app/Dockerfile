FROM python:3.12-slim
RUN pip install --no-cache-dir Flask>=3.0
WORKDIR /app
COPY app.py .
EXPOSE 5000
CMD ["python", "app.py"]

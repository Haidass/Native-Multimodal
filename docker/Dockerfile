FROM python:3.12-slim
RUN useradd --create-home --uid 1000 curator
WORKDIR /app
COPY pyproject.toml README.md ./
COPY image_cleaner ./image_cleaner
RUN python -m pip install --no-cache-dir .
USER curator
ENTRYPOINT ["python", "-m", "image_cleaner"]
CMD ["--help"]

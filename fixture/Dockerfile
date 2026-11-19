# Demo fixture: serves a fake look-alike page inside the collector's private
# network. Standard library only; nothing to install.
FROM python:3.12-alpine

WORKDIR /fixture
COPY server.py ./
COPY static/ ./static/
WORKDIR /fixture/static

EXPOSE 8000
CMD ["python", "../server.py"]

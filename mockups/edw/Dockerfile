FROM python:3.11-slim
WORKDIR /app
RUN pip install fastapi uvicorn[standard]
COPY mockups/edw/app.py /app/mockups/edw/app.py
COPY mockups/__init__.py /app/mockups/__init__.py
COPY mockups/edw/__init__.py /app/mockups/edw/__init__.py
EXPOSE 8002
CMD ["python", "-m", "mockups.edw.app"]

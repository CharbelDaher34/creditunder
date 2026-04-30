FROM python:3.11-slim
WORKDIR /app
RUN pip install fastapi uvicorn[standard]
COPY mockups/dms/app.py /app/mockups/dms/app.py
COPY mockups/__init__.py /app/mockups/__init__.py
COPY mockups/dms/__init__.py /app/mockups/dms/__init__.py
EXPOSE 8001
CMD ["python", "-m", "mockups.dms.app"]

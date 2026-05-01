FROM python:3.11-slim
WORKDIR /app
RUN pip install fastapi uvicorn[standard] aiokafka
COPY mockups/crm/app.py /app/mockups/crm/app.py
COPY mockups/__init__.py /app/mockups/__init__.py
COPY mockups/crm/__init__.py /app/mockups/crm/__init__.py
EXPOSE 8003
CMD ["python", "-m", "mockups.crm.app"]

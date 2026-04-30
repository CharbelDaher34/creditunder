"""
Sample Kafka events representing Siebel CRM submissions.
Document IDs reference documents pre-seeded in the DMS mockup.
"""

SAMPLE_EVENTS = [
    {
        "event_id": "f7c3a1b2-4d5e-4a6f-8b9c-0d1e2f3a4b5c",
        "application_id": "APP-2026-089341",
        "product_type": "PERSONAL_FINANCE",
        "branch_name": "Riyadh Main Branch",
        "validator_id": "CRM-USR-4821",
        "supervisor_id": "CRM-USR-1093",
        # DMS-00192 = ID document, DMS-00193 = salary certificate
        "document_ids": ["DMS-00192", "DMS-00193"],
        "applicant_data": {
            "name": "Mohammed Al-Harbi",
            "id_number": "1082345678",
            "date_of_birth": "1985-04-12",
            "employer": "Saudi Aramco",
            "declared_salary": 18500.00,
            "simah_score": 720,
            "t24_account_id": "T24-ACC-998821",
            "requested_amount": 150000.00,
            "requested_tenure_months": 60,
        },
    },
    # Second event: salary mismatch scenario
    {
        "event_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "application_id": "APP-2026-089342",
        "product_type": "PERSONAL_FINANCE",
        "branch_name": "Jeddah Branch",
        "validator_id": "CRM-USR-5533",
        "supervisor_id": "CRM-USR-1093",
        "document_ids": ["DMS-00192", "DMS-00193"],
        "applicant_data": {
            "name": "Mohammed Al-Harbi",
            "id_number": "1082345678",
            "date_of_birth": "1985-04-12",
            "employer": "Saudi Aramco",
            # Intentionally different from the 18,500 in the certificate → SOFT_MISMATCH
            "declared_salary": 25000.00,
            "simah_score": 680,
            "t24_account_id": "T24-ACC-112233",
            "requested_amount": 200000.00,
            "requested_tenure_months": 84,
        },
    },
]

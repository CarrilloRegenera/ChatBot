import pyodbc

def get_connection():
    conn = pyodbc.connect(
        "Driver={ODBC Driver 17 for SQL Server};"
        "Server=localhost\\SQLEXPRESS;"
        "Database=ChatBot;"
        "Trusted_Connection=yes;"
    )
    return conn
"""
Direct SQL migration script to add subject_id to Quiz table
"""

import pymysql
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Get database credentials from environment
DB_HOST = os.getenv("MYSQL_HOST", "localhost")
DB_PORT = int(os.getenv("MYSQL_PORT", 3306))
DB_USER = os.getenv("MYSQL_USER", "root")
DB_PASSWORD = os.getenv("MYSQL_PASSWORD", "1234")
DB_NAME = os.getenv("MYSQL_DATABASE", "learning_assistance")


def run_migration():
    # Connect to database
    try:
        connection = pymysql.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
        )

        print(f"Connected to database {DB_NAME} on {DB_HOST}:{DB_PORT}")

        cursor = connection.cursor()

        # Check if column exists
        cursor.execute(
            """
        SELECT column_name
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = 'quizzes' AND COLUMN_NAME = 'subject_id';
        """
        )

        column_exists = cursor.fetchone() is not None

        if not column_exists:
            print("Adding subject_id column to quizzes table...")

            # Add the column with a default value and foreign key
            cursor.execute(
                """
            ALTER TABLE quizzes 
            ADD COLUMN subject_id INTEGER NOT NULL DEFAULT 1;
            """
            )

            # Add foreign key in a separate statement
            cursor.execute(
                """
            ALTER TABLE quizzes
            ADD CONSTRAINT fk_quiz_subject FOREIGN KEY (subject_id) REFERENCES subjects(id);
            """
            )

            connection.commit()
            print("Column added successfully!")
        else:
            print("Column already exists. No action needed.")

    except Exception as e:
        print(f"Error during migration: {e}")
        if "connection" in locals():
            connection.rollback()
    finally:
        if "connection" in locals():
            connection.close()
            print("Database connection closed.")


if __name__ == "__main__":
    run_migration()

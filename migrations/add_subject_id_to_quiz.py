"""
Manual migration script to add subject_id to Quiz model
"""

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from app import create_app, db
from app.models import Quiz
import pymysql


def run_migration():
    # Create app context
    app = create_app()

    # Check if column exists in the database
    with app.app_context():
        connection = db.engine.raw_connection()
        try:
            cursor = connection.cursor()
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
                # Add the column
                cursor.execute(
                    """
                ALTER TABLE quizzes 
                ADD COLUMN subject_id INTEGER NOT NULL DEFAULT 1,
                ADD CONSTRAINT fk_quiz_subject FOREIGN KEY (subject_id) REFERENCES subjects(id);
                """
                )
                connection.commit()
                print("Column added successfully!")
            else:
                print("Column already exists. No action needed.")

        except Exception as e:
            print(f"Error during migration: {e}")
            connection.rollback()
        finally:
            connection.close()


if __name__ == "__main__":
    run_migration()

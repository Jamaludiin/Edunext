from flask_wtf import FlaskForm
from wtforms import (
    StringField,
    TextAreaField,
    IntegerField,
    BooleanField,
    SelectField,
    FieldList,
    FormField,
    HiddenField,
)
from wtforms.validators import (
    DataRequired,
    Length,
    NumberRange,
    Optional,
    ValidationError,
)


class AnswerForm(FlaskForm):
    """Form for adding/editing quiz answers."""

    text = StringField(
        "Answer Text", validators=[DataRequired(), Length(min=1, max=500)]
    )
    is_correct = BooleanField("Correct Answer")
    id = HiddenField("Answer ID")  # For editing existing answers


class QuestionForm(FlaskForm):
    """Form for adding/editing quiz questions."""

    text = TextAreaField(
        "Question Text", validators=[DataRequired(), Length(min=10, max=2000)]
    )
    difficulty_level = SelectField(
        "Difficulty Level",
        choices=[
            (1, "Very Easy"),
            (2, "Easy"),
            (3, "Medium"),
            (4, "Hard"),
            (5, "Very Hard"),
        ],
        coerce=int,
        validators=[DataRequired()],
    )
    id = HiddenField("Question ID")  # For editing existing questions


class QuizForm(FlaskForm):
    """Form for adding/editing quizzes."""

    title = StringField(
        "Quiz Title", validators=[DataRequired(), Length(min=5, max=200)]
    )
    description = TextAreaField(
        "Description", validators=[Optional(), Length(max=2000)]
    )
    subject_id = SelectField("Subject", coerce=int, validators=[DataRequired()])

    def __init__(self, *args, **kwargs):
        super(QuizForm, self).__init__(*args, **kwargs)
        # subjects will be populated in the route function

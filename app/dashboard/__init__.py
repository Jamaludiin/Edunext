from flask import Flask

app = Flask(__name__)

from app import routes  # make sure this line exists to register your routes

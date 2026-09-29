from flask import Flask, render_template

app = Flask(__name__, template_folder='./../frontend', static_folder="../frontend/static")

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/users")
def users():
    return render_template("users.html")


@app.route("/events")
def events():
    return render_template("events.html")


@app.route("/projects")
def projects():
    return render_template("projects.html")


@app.route("/judging")
def judging():
    return render_template("judging.html")



if __name__ == "__main__":
    app.run(debug=True)
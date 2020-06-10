# Simple backend server using flask framework

from flask import Flask, jsonify, request
from flask_cors import CORS, cross_origin

app = Flask(__name__)
CORS(app)

'''
    Get sample data from local file with a fixed output lines
    TODO: too naive, improvement required.
'''
@app.route('/data')
def getData():
    fn = 'Power_sample_data.csv'
    numLines = 7200000  # assume number of records is accessible upon deployment
    maxLines = request.args.get('number', default=6000, type=int)
    frequency = numLines / maxLines
    data = list()
    with open(fn, 'r') as fr:
        for i, line in enumerate(fr):
            if i % frequency == 0:
                data.append(line.strip('\n').split(','))
    return jsonify(data)


@app.route("/")
def hello():
    return "Hello world, welcome to Google"


if __name__ == '__main__':
    app.run(port=5000)

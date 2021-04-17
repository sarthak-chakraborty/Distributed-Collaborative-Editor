# Distributed Collaborative Editor

This is term project for the course Distributed Systems(CS60002) [IIT Kgp]. Contributors for this project are:

- ![Sarthak Chakraborty](https://github.com/sarthak-chakraborty)
- ![Sai Saketh Aluru](https://github.com/SaiSakethAluru)
- ![Nikhil Nayan Jha](https://github.com/nnjha98)
- ![Omar Eqbal](https://github.com/omareqbal)
- ![Aditya Anand](https://github.com/adiabhi1998)


### Basic Information

This is a Collaborative editor that uses Operational Transformations to maintain consistency in the documents at server and client side. OT algorithms and its code is based on [Tim Baumann's project](https://github.com/Operational-Transformation).

Our system has a single `MASTER` server, and 2 worker servers, among which one will act as a `PRIMARY` while the others act as a `SECONDARY`.

Django app has been used for all the SERVERS while CLIENT text area is written in javascript and uses CodeMirror. Updates can be sent over Fanout Cloud to get real-time streaming guarantees.

We have implemented a basic collaborative editor that can handle multiple documents, supports a `single` crash fault, along with recovery mechanism. The replication strategy that we have used is Passive Replication.


## Usage

Install dependencies and setup database for all the servers:

```sh
virtualenv venv
. venv/bin/activate
cd master
pip install -r requirements.txt
python manage.py migrate

cd ../worker-primary
python manage.py migrate

cd ../worker-secondary
python manage.py migrate
cd ..
```

Note: default storage is sqlite.

Install dependencies for proxy server:

```sh
cd proxy-server/
npm install
cd ..
```

### Running with Fanout Cloud

Create a `.env` file in each of the directories(`master` / `worker-primary` / `worker-secondary` ) containing `GRIP_URL`:

```sh
GRIP_URL=https://api.fanout.io/realm/{realm-id}?iss={realm-id}&key=base64:{realm-key}
```

Be sure to replace `{realm-id}` and `{realm-key}` with the values from the Fanout control panel.

In a separate shell, run proxy server for local tunneling:

```sh
node proxy-server/proxy.js
```

In the Fanout control panel, set the public IP of the running system `<host>:<port>` as the Origin Server. (Since the proxy server is running at `PORT=8000`, `<port>` at the Fanout control panel will be `8000` as well)

Now run a local instance of all the servers in separate shell

```sh
cd master/
python manage.py runserver 127.0.0.1:8001
```

```sh
cd worker-primary/
python manage.py runserver 127.0.0.1:8002
```

```sh
cd worker-secondary/
python manage.py runserver 127.0.0.1:8003
```

Then open up two browser windows to your Fanout Cloud domain (e.g. `http://{realm-id}.fanoutcdn.com/`). Requests made to Fanout Cloud should be routed through the procy server to the local instances of the server running. New document can be created by going to the URL `http://{realm-id}.fanoutcdn.com/{new-document-ID}`


### Recovery Handling

For recovery handling, the local instance running as the `PRIMARY` server (S1) can be stopped(representing as a crash fault) and can then be restarted again(at the same port). It can be seen that S1 recovers with the updates that had been applied while it was down becomes a secondary server. Consistency checks can be done by crashing the current primary server (not S1), which will lead S1 to be primary.

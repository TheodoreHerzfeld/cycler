__version__ = "0.2.1"

import kubernetes, subprocess, json, threading, time, os, yaml, signal, logging, http.server, socketserver

from typing import Tuple
from http import HTTPStatus

from discord_webhook import DiscordWebhook

LOG_LEVELS = {
    "CRITICAL": logging.CRITICAL,
    "FATAL": logging.FATAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "WARN": logging.WARN,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG
}

timeouts = {}

checks = {
    "config": False,
    "threads": False,
    "kubeconfig": False,
}

class Healthcheck(http.server.SimpleHTTPRequestHandler):

    def __init__(self, request: bytes, client_address: Tuple[str, int], server: socketserver.BaseServer):
        super().__init__(request, client_address, server)

    @property
    def api_response(self):
        global checks
        return json.dumps(checks).encode()
    
    def log_message(self, format, *args):
        global hcLog
        if hcLog:
            super().log_message(format, *args)
        else:
            pass
    
    def do_GET(self):
        if self.path == '/healthz':
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(bytes(self.api_response))
            return
        
        self.send_response(HTTPStatus.NOT_FOUND)

def await_restart(namespace, name):
    global timeouts
    timeouts[f"{namespace}.{name}"] = True
    subprocess.run(f"kubectl rollout status deployment {name} -n {namespace}", shell=True, check=True, timeout=restartTimeout, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        timeouts.pop(f"{namespace}.{name}")
    except KeyError:
        pass # this is normal - multiple awaits can be called simultaineously

def get_pods(client, namespace, selector):
    try:
        normal_selector = ""
        for i in selector:
            normal_selector += f"{i}={selector[i]},"
        normal_selector = normal_selector.rstrip(",")
        return client.list_namespaced_pod(namespace, label_selector=normal_selector).items
    except TypeError:
        return []

def restart_deployment(namespace, name):
    global logger
    logger.info(f"triggering restart of {namespace}.{name}")
    try:
        subprocess.check_output(f"kubectl -n {namespace} rollout restart deployment {name}", shell=True, timeout=3, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as exc:
        logger.warning(f"Error calling kubectl rollout restart for {namespace}.{name}: {str(exc.stdout, encoding='utf-8').strip()}")
    threading.Thread(target=await_restart, args=(namespace, name), daemon=True).start()
    
    try:
        if notifications["discord"]:
            logger.debug("sending notification to discord")
            DiscordWebhook(url=notifications["discord"], content=f"Cycles has triggered a restart for {namespace}.{name}").execute()
    except Exception as e:
        logger.error(f"error sending notification: {e}")
    
def get_deployments(appClient, coreClient):
    for deployment in appClient.list_deployment_for_all_namespaces(watch=False).items:
        if stopNow:
            logger.info("bye!")
            exit(0)
        if ("app.cycler.io/enable" in deployment.metadata.annotations):
                logger.debug(f"Scanning {deployment.metadata.namespace}.{deployment.metadata.name}")
                if (deployment.status.unavailable_replicas != None) and (f"{deployment.metadata.namespace}.{deployment.metadata.name}" not in timeouts):
                    logger.info(f"Detected restart of {deployment.metadata.namespace}.{deployment.metadata.name}")
                    threading.Thread(target=await_restart, args=(deployment.metadata.namespace, deployment.metadata.name), daemon=True).start()
                else:
                    for image in get_pods(coreClient, deployment.metadata.namespace, deployment.spec.selector.match_labels)[0].status.container_statuses:
                        if (image.image.split("/")[0] not in times) or (times[image.image.split("/")[0]] <= 0):
                            try:
                                logger.debug(f"checking {image.image}")
                                registrySHA = get_sha(image.image)
                                if registrySHA == None:
                                    logger.info(f"skipping {image.image} due to errors retreiving registry SHA")
                                elif image.image_id.split("@sha256:")[1] != registrySHA and (f"{deployment.metadata.namespace}.{deployment.metadata.name}" not in timeouts):
                                    logger.info(f"sha mismatch found for {image.image}; live: {image.image_id.split("@sha256:")[1]}; registry: {registrySHA}")
                                    threading.Thread(target=restart_deployment, args=(deployment.metadata.namespace, deployment.metadata.name)).start()
                            except IndexError:
                                logger.info("ignorring normal indexing error - likely a non-atomic operation")
                        else:
                            logger.debug(f"delaying checking {image.image} because of rate limit settings ({times[image.image.split("/")[0]]} seconds until next check)")
    for time in times:
        if times[time] <= 0:
            times[time] = rates[time]                 

def get_sha(url):
    try:
        req = subprocess.run(f"skopeo inspect docker://{url} --authfile {secretsFile}", shell=True, capture_output=True, text=True, timeout=registryTimeout)
    except subprocess.TimeoutExpired:
        logger.info(f"timeout expired for {url}. You're probably being rate-limited")
        return None
    if req.returncode == 0:
        info = json.loads(req.stdout)
        return info["Digest"].replace("sha256:", "")
    else:
        logger.info(f"SKOPEO ERROR: {req.stderr}")
        return None

def tick():
    while True:
        for set in times:
            times[set] -= 1
        time.sleep(1)
        logger.debug(f"tick {times}")
        
def exitGracefully(signum, frame):
    logger.info("exiting gracefully")

    global stopNow
    stopNow = True

def main():
    global logger
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler) 

    global checks
    logger.info(f"Cycler is starting (v{__version__})")
    
    signal.signal(signal.SIGINT, exitGracefully)
    signal.signal(signal.SIGTERM, exitGracefully)
    
    #DEFAULTS
    configPath = os.getenv("CYCLER_CONFIG") or "/etc/cycler/config.yml"
    scanDelay = 5

    global registryTimeout
    global secretsFile
    global restartTimeout
    global logLevel   
    global notifications
    global times
    global stopNow
    global hcLog

    stopNow = False
    
    with open(configPath) as stream:
        try:
            logger.info(f"Loading cycles config from {configPath}")
            config = yaml.safe_load(stream)
        except yaml.YAMLError as exc:
            logger.fatal(f"ERROR LOADING CONFIG FROM {configPath}")
            logger.fatal(exc)
            
    # SET DEFAULTS
    scanDelay = config.get("scanDelay", 300)
    registryTimeout = config.get("registryTimeout", 5)
    secretsFile = config.get("secretsFile", "/etc/secrets/.dockerconfigjson")
    restartTimeout = config.get("restartTimeout", 60)
    logLevel = LOG_LEVELS[config.get("loglevel", "DEBUG")]
    notifications = config.get("notifications", None)
    startRated = config.get("startRated", False)
    hcPort = config.get("hcPort", 8080)
    hcLog = config.get("hcLog", False)

    logger.setLevel(logLevel)
    handler.setLevel(logLevel)

    global rates
    rates = config.get("rates", {})
    if startRated:
        logger.info("starting with rates set")
        times = dict(rates)
    else:
        times = { i: 0 for i in rates }
        logger.info(f"rate times: {times}")

    ###
    checks["config"] = True
    ###

    logger.info("Loading kubeconfig from the cluster")
    kubernetes.config.load_incluster_config() 
    appClient = kubernetes.client.AppsV1Api()
    coreClient = kubernetes.client.CoreV1Api()

    ###
    checks["kubeconfig"] = True
    ###
    
    logger.info("spawning background threads")
    threading.Thread(target=tick, daemon=True).start()
    
    ###
    checks["threads"] = True
    ###
 
    logger.info("creating healthcheck endpoint")
    global hc_server
    hc_server = socketserver.TCPServer(("0.0.0.0", hcPort), Healthcheck)
    threading.Thread(target=hc_server.serve_forever, daemon=True).start()
    logger.info(f"healthcheck listening at 0.0.0.0:{hcPort}/healthz")

    logger.info("starting scan loop")

    # main daemon loop
    while not stopNow:
        logger.debug("starting scan")
        get_deployments(appClient, coreClient)
        logger.debug(f"scan complete, waiting {scanDelay} seconds...")
        time.sleep(scanDelay)
    logger.info("bye!")
        
if __name__ == "__main__":
    main()
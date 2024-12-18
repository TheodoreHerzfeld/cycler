import kubernetes, subprocess, json, threading, time, os, yaml, signal
from discord_webhook import DiscordWebhook

timeouts = {}

stopNow = False
def getstopNow() -> bool:
    return stopNow

def log(level, line):
    if level == logLevel:
        print(line)
    if level == "INFO":
        print(line)

def await_restart(namespace, name):
    global timeouts
    timeouts[f"{namespace}.{name}"] = True
    subprocess.run(f"kubectl rollout status deployment {name} -n {namespace}", shell=True, check=True, timeout=restartTimeout, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    timeouts.pop(f"{namespace}.{name}")

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
    subprocess.run(f"kubectl -n {namespace} rollout restart deployment {name}", shell=True, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    threading.Thread(target=await_restart, args=(namespace, name), daemon=True).start()
    
    try:
        if notifications["discord"]:
            log("DEBUG", "sending notification to discord")
            DiscordWebhook(url=notifications["discord"], content=f"Cycles has triggered a restart for {namespace}.{name}").execute()
    except Exception as e:
        print(f"error sending notification: {e}")
    
def get_deployments(appClient, coreClient):
    for deployment in appClient.list_deployment_for_all_namespaces(watch=False).items:
        if stopNow:
            print("bye!")
            exit(0)
        if ("app.cycler.io/enable" in deployment.metadata.annotations) and (f"{deployment.metadata.namespace}.{deployment.metadata.name}" not in timeouts):
                log("DEBUG", "")
                log("DEBUG", f"Scanning {deployment.metadata.namespace}.{deployment.metadata.name}")
                log("DEBUG", "====================================================================")
                if deployment.status.unavailable_replicas != None:
                    log("INFO", f"Detected restart of {deployment.metadata.namespace}.{deployment.metadata.name}")
                    threading.Thread(target=await_restart, args=(deployment.metadata.namespace, deployment.metadata.name), daemon=True).start()
                else:
                    for image in get_pods(coreClient, deployment.metadata.namespace, deployment.spec.selector.match_labels)[0].status.container_statuses:
                        if (image.image.split("/")[0] not in times) or (times[image.image.split("/")[0]] <= 0):
                            try:
                                log("DEBUG", f"checking {image.image}")
                                registrySHA = get_sha(image.image)
                                if registrySHA == None:
                                    print(f"skipping {image.image} due to errors retreiving registry SHA")
                                elif image.image_id.split("@sha256:")[1] != registrySHA:
                                    log("INFO", f"sha mismatch found for {image.image}:")
                                    log("INFO", f"live:     {image.image_id.split("@sha256:")[1]}")
                                    log("INFO", f"registry: {registrySHA}")
                                    print(f"restarting {deployment.metadata.namespace}.{deployment.metadata.name}")
                                    threading.Thread(target=restart_deployment, args=(deployment.metadata.namespace, deployment.metadata.name)).start()
                            except IndexError:
                                print("ignoring normal index error")
                        else:
                            log("DEBUG", f"delaying checking {image.image} because of rate limit settings ({times[image.image.split("/")[0]]} seconds until next check)")
                for time in times:
                    if times[time] <= 0:
                        times[time] = rates[time]                 

def get_sha(url):
    try:
        req = subprocess.run(f"skopeo inspect docker://{url} --authfile {secretsFile}", shell=True, capture_output=True, text=True, timeout=registryTimeout)
    except subprocess.TimeoutExpired:
        print(f"timeout expired for {url}. You're probably being rate-limited")
        return None
    if req.returncode == 0:
        info = json.loads(req.stdout)
        return info["Digest"].replace("sha256:", "")
    else:
        print(f"SKOPEO ERROR: {req.stderr}")
        return None

def tick():
    while True:
        for set in times:
            times[set] -= 1
        time.sleep(1)
        
def exitGracefully(signum, frame):
    print("exiting gracefully...")
    global stopNow
    stopNow = True

def main():
    print("Cycler is starting")
    
    signal.signal(signal.SIGINT, exitGracefully)
    signal.signal(signal.SIGTERM, exitGracefully)
    
    configPath = os.getenv("CYCLER_CONFIG") or "/etc/cycler/config.yml"
    
    #DEFAULTS
    scanDelay = 5
    global registryTimeout
    global secretsFile
    global restartTimeout
    global logLevel   
    global notifications
    global times
    
    with open(configPath) as stream:
        try:
            print(f"Loading cycles config from {configPath}")
            config = yaml.safe_load(stream)
        except yaml.YAMLError as exc:
            print(f"ERROR LOADING CONFIG FROM {configPath}")
            print(exc)
            
    # SET DEFAULTS
    scanDelay = config.get("scanDelay", 300)
    registryTimeout = config.get("registryTimeout", 5)
    secretsFile = config.get("secretsFile", "/etc/secrets/.dockerconfigjson")
    restartTimeout = config.get("restartTimeout", 60)
    logLevel = config.get("loglevel", "DEBUG")
    notifications = config.get("notifications", None)
    startRated = config.get("startRated", False)
    
    
    global rates
    rates = config.get("rates", {})
    if startRated:
        times = dict(rates)
    else:
        times = { i: 0 for i in rates }
    
    print("Loading kubeconfig from the cluster...", end="")
    kubernetes.config.load_incluster_config() 
    appClient = kubernetes.client.AppsV1Api()
    coreClient = kubernetes.client.CoreV1Api()
    print("done")
    
    print("spawning background threads...", end="")
    threading.Thread(target=tick, daemon=True).start()
    print("done")
    
    print("started scanning for changes")
    
    # main daemon loop
    while not stopNow:
        log("DEBUG", "starting scan")
        get_deployments(appClient, coreClient)
        log("DEBUG", "")
        log("DEBUG", f"scan complete, waiting {scanDelay} seconds...")
        time.sleep(scanDelay)
    print("bye!")
        
if __name__ == "__main__":
    main()
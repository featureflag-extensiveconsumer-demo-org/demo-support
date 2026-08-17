import json
import os
import signal
import sys
import time
from datetime import datetime, timezone

import ldclient
from ldclient.config import Config
from ldclient.context import Context

REPOSITORY = "demo-support"
RELEASE = "v002"
FLAGS = ["demo-support-assistant"]

CLUSTERS = json.loads(r'''{"production":[{"key":"prod-eu-west-02","name":"Production EU West 02","environment":"production","region":"eu-west","ordinal":2,"releaseRing":"canary","weight":5},{"key":"prod-sa-east-02","name":"Production South America East 02","environment":"production","region":"sa-east","ordinal":2,"releaseRing":"stable","weight":10},{"key":"prod-us-east-02","name":"Production US East 02","environment":"production","region":"us-east","ordinal":2,"releaseRing":"stable","weight":15},{"key":"prod-emea-central-04","name":"Production EMEA Central 04","environment":"production","region":"emea-central","ordinal":4,"releaseRing":"stable","weight":30},{"key":"prod-eu-west-01","name":"Production EU West 01","environment":"production","region":"eu-west","ordinal":1,"releaseRing":"stable","weight":40}],"staging":[{"key":"stg-eu-central-02","name":"Staging EU Central 02","environment":"staging","region":"eu-central","ordinal":2,"releaseRing":"canary","weight":40},{"key":"stg-eu-central-01","name":"Staging EU Central 01","environment":"staging","region":"eu-central","ordinal":1,"releaseRing":"stable","weight":60}],"test":[{"key":"test-eu-central-02","name":"Test EU Central 02","environment":"test","region":"eu-central","ordinal":2,"releaseRing":"canary","weight":25},{"key":"test-eu-central-01","name":"Test EU Central 01","environment":"test","region":"eu-central","ordinal":1,"releaseRing":"stable","weight":75}],"dev":[{"key":"dev-local-01","name":"Development Local 01","environment":"dev","region":"local","ordinal":1,"releaseRing":"stable","weight":100}]}''')
PROFILES = json.loads(r'''{"production":{"enterprise":10,"beta":15,"legacy":8,"busy":100,"quiet":40},"staging":{"enterprise":20,"beta":30,"legacy":20,"busy":30,"quiet":12},"test":{"enterprise":30,"beta":35,"legacy":30,"busy":10,"quiet":4},"dev":{"enterprise":15,"beta":25,"legacy":12,"busy":2,"quiet":1}}''')
OFFSETS = json.loads(r'''{"demo-orders":11,"demo-storefront":43,"demo-profile":71}''')

stop_requested = False


def request_stop(_signum, _frame):
    global stop_requested
    stop_requested = True


def offset_for(name):
    if name in OFFSETS:
        return OFFSETS[name]
    value = 7
    for char in name:
        value = (value * 31 + ord(char)) % 100
    return value


def batch_size(profile, at):
    settings = PROFILES[profile]
    business = at.weekday() <= 4 and 7 <= at.hour < 19
    return settings["busy"] if business else settings["quiet"]


def cluster_for(name, environment, index):
    choices = CLUSTERS[environment]
    bucket = (index * 17 + offset_for(name)) % 100
    boundary = 0
    for item in choices:
        boundary += item["weight"]
        if bucket < boundary:
            return item
    return choices[-1]


def context_for_traffic(name, environment, index, generation):
    settings = PROFILES[environment]
    bucket = (index * 37 + offset_for(name)) % 100
    plan, region, cohort = "free", "eu", "control"
    if bucket < settings["enterprise"]:
        plan = "enterprise"
    elif bucket < settings["enterprise"] + settings["beta"]:
        cohort = "checkout-beta"
    user = (Context.builder("%s-%s-%d" % (name, environment, index % 1000)).kind("user")
            .set("plan", plan).set("region", region).set("cohort", cohort).build())
    selected = cluster_for(name, environment, index)
    service = Context.builder(name).kind("service").set("name", name).build()
    cluster = (Context.builder(selected["key"]).kind("cluster")
               .set("name", selected["name"]).set("environment", selected["environment"])
               .set("region", selected["region"]).set("ordinal", selected["ordinal"])
               .set("releaseRing", selected["releaseRing"]).set("generation", generation).build())
    return Context.create_multi(user, service, cluster)


def main():
    sdk_key = os.environ.get("LD_EVALUATION_SDK_KEY")
    if not sdk_key:
        print("LD_EVALUATION_SDK_KEY is required.", file=sys.stderr)
        sys.exit(1)
    environment = os.environ.get("DEMO_ENVIRONMENT")
    if "--profile" in sys.argv:
        environment = sys.argv[sys.argv.index("--profile") + 1]
    if environment not in PROFILES:
        print("A valid --profile is required.", file=sys.stderr)
        sys.exit(1)
    generation = os.environ.get("DEMO_GENERATION_ID", "untracked")
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    index = 0
    while not stop_requested:
        opened_at = time.time()
        ldclient.set_config(Config(sdk_key, application={"id": REPOSITORY, "version": RELEASE}))
        client = ldclient.get()
        count = batch_size(environment, datetime.now(timezone.utc))
        per_flag = {flag: {"true": 0, "false": 0} for flag in FLAGS}
        clusters = {}
        attempted = 0
        for item in range(count):
            context = context_for_traffic(REPOSITORY, environment, index + item, generation)
            for flag in FLAGS:
                value = client.variation(flag, context, False)
                per_flag[flag]["true" if value else "false"] += 1
                attempted += 1
            key = cluster_for(REPOSITORY, environment, index + item)["key"]
            clusters[key] = clusters.get(key, 0) + 1
        client.flush()
        print(json.dumps({
            "type": "traffic-batch", "repository": REPOSITORY, "release": RELEASE,
            "flags": FLAGS, "perFlag": per_flag, "profile": environment,
            "generation": generation, "contexts": count, "attempted": attempted,
            "clusters": clusters, "flush": "ok",
            "connectionMs": int((time.time() - opened_at) * 1000),
        }), flush=True)
        client.close()
        index += count
        for _ in range(300):
            if stop_requested:
                break
            time.sleep(1)


if __name__ == "__main__":
    main()

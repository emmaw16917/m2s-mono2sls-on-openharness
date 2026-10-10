import json, sys, urllib.request, urllib.error
import boto3

R = "us-east-1"
cf = boto3.client("cloudformation", region_name=R)
out = {o["OutputKey"]: o["OutputValue"] for o in cf.describe_stacks(StackName="m2s-jwt-probe")["Stacks"][0]["Outputs"]}
idp = boto3.client("cognito-idp", region_name=R)
pool, client = out["PoolId"], out["ClientId"]
user, pw = "probe@example.com", "Probe-Passw0rd!x"
try:
    idp.admin_create_user(UserPoolId=pool, Username=user, MessageAction="SUPPRESS",
                          UserAttributes=[{"Name": "email", "Value": user}, {"Name": "email_verified", "Value": "true"}])
except idp.exceptions.UsernameExistsException:
    pass
idp.admin_set_user_password(UserPoolId=pool, Username=user, Password=pw, Permanent=True)
for g in ("admins", "staff"):
    idp.admin_add_user_to_group(UserPoolId=pool, Username=user, GroupName=g)
tok = idp.initiate_auth(ClientId=client, AuthFlow="USER_PASSWORD_AUTH", AuthParameters={"USERNAME": user, "PASSWORD": pw})["AuthenticationResult"]
ID, AC = tok["IdToken"], tok["AccessToken"]


def call(url, auth=None, origin=None):
    h = {}
    if auth is not None:
        h["Authorization"] = auth
    if origin:
        h["Origin"] = origin
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, dict(r.headers), r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode()


H, RS = out["HttpUrl"], out["RestUrl"]
cases = [
    ("http v1 id raw", H + "/v1", ID, None),
    ("http v1 id bearer", H + "/v1", "Bearer " + ID, None),
    ("http v1 access raw", H + "/v1", AC, None),
    ("http v1 access bearer", H + "/v1", "Bearer " + AC, None),
    ("http v2 id bearer", H + "/v2", "Bearer " + ID, None),
    ("http v1 anon", H + "/v1", None, "https://app.example.com"),
    ("http v1 bad token", H + "/v1", "Bearer x.y.z", "https://app.example.com"),
    ("http pub anon", H + "/pub", None, "https://app.example.com"),
    ("http pub with token", H + "/pub", "Bearer " + ID, None),
    ("rest v1 id raw", RS + "/v1", ID, None),
    ("rest v1 id bearer", RS + "/v1", "Bearer " + ID, None),
    ("rest v1 access raw", RS + "/v1", AC, None),
]
res = {}
for name, url, auth, origin in cases:
    st, hd, body = call(url, auth, origin)
    try:
        b = json.loads(body)
    except ValueError:
        b = body
    ac = (b.get("requestContext") or {}).get("authorizer") if isinstance(b, dict) else None
    res[name] = {"status": st, "cors_headers": {k: v for k, v in hd.items() if k.lower().startswith("access-control")},
                 "authorizer": ac, "body": b if st != 200 else None, "version": b.get("version") if isinstance(b, dict) else None}
json.dump(res, open(sys.argv[1], "w"), indent=2)
for k, v in res.items():
    print(k, v["status"], "cors=" + json.dumps(v["cors_headers"]), "authorizer_keys=" + json.dumps(list((v["authorizer"] or {}).keys())))

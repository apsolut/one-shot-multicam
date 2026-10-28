"""Download Comfy Cloud's node definitions (/api/object_info, ~10 MB) to object_info.json next to this file.
Reads COMFY_API_KEY from the environment and never prints it. The file is not committed."""
import json, os, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__))
req = urllib.request.Request("https://cloud.comfy.org/api/object_info", headers={"X-API-Key": os.environ["COMFY_API_KEY"]})
data = json.load(urllib.request.urlopen(req, timeout=120))
json.dump(data, open(os.path.join(HERE, "object_info.json"), "w"))
print(len(data), "node classes")

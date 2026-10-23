# Creates the "H.264 -> HEVC (QSV)" flow and attaches it to the Movies library, paused.
# Run on the gpu VM: sudo python3 tdarr_setup.py [--start]
import json, sys, urllib.request
K = open('/root/tdarr-api-key').read().strip()
def db(collection, mode, docID=None, obj=None):
    data = {'collection': collection, 'mode': mode}
    if docID: data['docID'] = docID
    if obj is not None: data['obj'] = obj
    r = urllib.request.Request('http://localhost:8266/api/v2/cruddb', method='POST',
        data=json.dumps({'data': data}).encode(), headers={'Content-Type': 'application/json', 'x-api-key': K})
    body = urllib.request.urlopen(r, timeout=60).read()
    return json.loads(body) if body else None
CODE = """module.exports = async (args) => {
  // Logic lives in /app/configs/hevc/flow.js (homelab: tdarr-review/scripts/hevc-flow.js).
  const f = '/app/configs/hevc/flow.js';
  delete require.cache[require.resolve(f)];
  return require(f)(args);
};
"""
flow = {
    '_id': 'hevcQsvFlow', 'name': 'H.264 -> HEVC (QSV, quality 22)',
    'description': 'Goobs 2026-10-21: 8-bit HEVC MKV, English + original language, >=20% smaller, Radarr rescan',
    'tags': '',
    'flowPlugins': [
        {'name': 'Input File', 'sourceRepo': 'Community', 'pluginName': 'inputFile', 'version': '1.0.0',
         'id': 'inHevc01', 'position': {'x': 400, 'y': 100}, 'fpEnabled': True, 'inputsDB': {}},
        {'name': 'Encode and replace (hevc-flow.js)', 'sourceRepo': 'Community', 'pluginName': 'customFunction',
         'version': '1.0.0', 'id': 'cfHevc01', 'position': {'x': 400, 'y': 250}, 'fpEnabled': True,
         'inputsDB': {'code': CODE}},
    ],
    'flowEdges': [{'source': 'inHevc01', 'sourceHandle': '1', 'target': 'cfHevc01', 'targetHandle': None, 'id': 'edHevc01'}],
}
if db('FlowsJSONDB', 'getById', 'hevcQsvFlow'): db('FlowsJSONDB', 'update', 'hevcQsvFlow', flow)
else: db('FlowsJSONDB', 'insert', 'hevcQsvFlow', flow)

lib = db('LibrarySettingsJSONDB', 'getById', '0AxeGqS_Z')
sched = [{'_id': s['_id'], 'checked': not (17 <= int(s['_id'].split(':')[1][:2]) < 23)} for s in lib['schedule']]
dm = dict(lib['decisionMaker'], settingsPlugin=False, settingsFlows=True, settingsVideo=False, settingsAudio=False)
start = '--start' in sys.argv
db('LibrarySettingsJSONDB', 'update', '0AxeGqS_Z', {
    'flowId': 'hevcQsvFlow', 'decisionMaker': dm, 'schedule': sched, 'cache': '/temp',
    'processLibrary': start, 'processTranscodes': True, 'processHealthChecks': False,
    'containerFilter': 'mkv,mp4,m4v,avi,ts,mov,wmv', 'scanOnStart': False, 'folderWatching': False,
    'foldersToIgnore': 'Sample,Samples,Extras,Featurettes,Trailers',
})
lib = db('LibrarySettingsJSONDB', 'getById', '0AxeGqS_Z')
print(json.dumps({k: lib[k] for k in ('flowId', 'processLibrary', 'cache', 'processHealthChecks', 'containerFilter')}),
      'hours off:', [s['_id'] for s in lib['schedule'] if not s['checked']][:7])

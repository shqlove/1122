// Verify every literal desktop call and Action against the real Python method signature.
const ts = require('typescript');
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const root = path.resolve(__dirname, '../..');
const calls = [];
for (const name of fs.readdirSync(path.join(root,'frontend/src')).filter(n=>n.endsWith('.tsx'))) {
  const source = ts.createSourceFile(name,fs.readFileSync(path.join(root,'frontend/src',name),'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
  function add(node,name,count) {calls.push({name,count,file:source.fileName,line:source.getLineAndCharacterOfPosition(node.getStart()).line+1});}
  function visit(node) {
    if (ts.isCallExpression(node) && node.expression.getText(source)==='api') {
      const [name,...args]=node.arguments;
      if (ts.isStringLiteral(name)) add(node,name.text,args.length);
      else if (!(ts.isIdentifier(name) && name.text==='name' && args.length===1 && ts.isSpreadElement(args[0]))) throw new Error('Unresolved API call: '+node.getText(source));
    }
    if ((ts.isJsxSelfClosingElement(node)||ts.isJsxOpeningElement(node)) && node.tagName.getText(source)==='Action') {
      const attrs=node.attributes.properties;
      const name=attrs.find(a=>a.name?.getText(source)==='name')?.initializer;
      const args=attrs.find(a=>a.name?.getText(source)==='args')?.initializer?.expression;
      if (!name || !ts.isStringLiteral(name) || (args && !ts.isArrayLiteralExpression(args))) throw new Error('Unresolved Action: '+node.getText(source));
      add(node,name.text,args?.elements.length || 0);
    }
    ts.forEachChild(node,visit);
  }
  visit(source);
}
const python=process.env.PYTHON || (process.platform==='win32'?path.join(root,'.venv/Scripts/python.exe'):'python');
const result=spawnSync(python,['-X','utf8','-c',`
import inspect,json,sys
from dialogue_tool.desktop import DesktopAPI
calls=json.load(sys.stdin)
for call in calls:
    method=getattr(DesktopAPI,call['name'],None)
    assert callable(method), call
    try: inspect.signature(method).bind(None,*([None]*call['count']))
    except TypeError as exc: raise AssertionError(f'{call}: {exc}') from exc
print(f"PASS: {len(calls)} UI call sites, {len(set(c['name'] for c in calls))} real desktop methods")
`],{cwd:root,input:JSON.stringify(calls),encoding:'utf8'});
process.stdout.write(result.stdout || '');process.stderr.write(result.stderr || '');
if (result.error) throw result.error;
process.exit(result.status ?? 1);

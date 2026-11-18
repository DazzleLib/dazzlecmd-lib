import json,sys,glob,os,re,collections
import regproto as R
files=sorted(glob.glob(os.path.expanduser('~/.claude/projects/*/*.jsonl')),key=os.path.getmtime)[-150:]
tot=0; c=collections.Counter(); ex=collections.defaultdict(list); seen=set()
for f in files:
    try: fh=open(f,encoding='utf-8',errors='replace')
    except OSError: continue
    for line in fh:
        if '"Bash"' not in line: continue
        try: o=json.loads(line)
        except Exception: continue
        m=o.get('message') or {}
        for b in (m.get('content') or []) if isinstance(m.get('content'),list) else []:
            if b.get('type')=='tool_use' and b.get('name')=='Bash':
                cmd=(b.get('input') or {}).get('command') or ''
                if cmd in seen: continue
                seen.add(cmd); tot+=1
                s=re.sub(r"""'[^']*'|"[^"]*\"""",'Q',cmd)
                parts=re.split(R.SEP['bash'],s); segs,seps=parts[0::2],parts[1::2]
                cl=[R.classify(x,x,'bash') for x in segs]
                hard=[i for i,x in enumerate(cl) if x and x[1]==R.U and not x[0].startswith('wrapper')]
                if not hard: continue
                if len(hard)>1: k='multi-unconditional'
                elif len(segs)==1: k='sole'
                else:
                    i=hard[0]; pre=[x.split()[:1] for x in segs[:i]]
                    t=segs[i].split()
                    lit=all(not re.search(r'[$*?~`]|\.\.|^/|^[A-Za-z]:',a) for a in t[1:] if not a.startswith('-'))
                    if all(sp=='&&' for sp in seps) and all(p==['cd'] for p in pre): k='cd-prefix &&-only, '+('literal-relative target' if lit else 'NON-literal target')
                    elif all(sp=='&&' for sp in seps): k='other &&-only chain'
                    else: k='chain with ; | || or newline'
                c[k]+=1
                if len(ex[k])<3: ex[k].append(cmd[:110].replace('\n','\n'))
print('files',len(files),'distinct Bash commands',tot)
for k,v in c.most_common(): print('%5d  %s'%(v,k)); [print('        e.g.',e) for e in ex[k]]

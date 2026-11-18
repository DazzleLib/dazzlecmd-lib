import json,glob,os,re,collections
import regproto as R
files=sorted(glob.glob(os.path.expanduser('~/.claude/projects/*/*.jsonl')),key=os.path.getmtime)[-150:]
TMP=re.compile(r'(?i)(/tmp\b|\$TMPDIR|\$TEMP\b|\$TMP\b|AppData[\/]+Local[\/]+Temp|scratchpad|mktemp)')
seen=set(); form=collections.Counter(); tgt=collections.Counter(); ex=collections.defaultdict(list); n=0
for f in files:
    try: fh=open(f,encoding='utf-8',errors='replace')
    except OSError: continue
    for line in fh:
        if '"Bash"' not in line: continue
        try: o=json.loads(line)
        except Exception: continue
        m=o.get('message') or {}; cont=m.get('content')
        for b in cont if isinstance(cont,list) else []:
            if b.get('type')!='tool_use' or b.get('name')!='Bash': continue
            cmd=(b.get('input') or {}).get('command') or ''
            if cmd in seen: continue
            seen.add(cmd)
            parts=re.split(R.SEP['bash'],cmd); segs,seps=parts[0::2],parts[1::2]
            s2=re.split(R.SEP['bash'],re.sub(r"""'[^']*'|"[^"]*\"""",'Q',cmd))
            if len(s2)!=len(parts): continue   # separator inside quotes: skip (counted below)
            cl=[R.classify(x,x,'bash') for x in s2[0::2]]
            hard=[i for i,x in enumerate(cl) if x and x[1]==R.U and not x[0].startswith('wrapper')]
            if not hard or len(s2[0::2])==1: continue
            if all(sp=='&&' for sp in seps) and len(hard)==1: continue
            n+=1
            assigns=dict(re.findall(r'(?m)(?:^|[;&\s])([A-Za-z_]\w*)=("[^"]*"|\'[^\']*\'|\$\([^)]*\)|\S+)',cmd))
            kinds=set()
            for i in hard:
                form[cl[i][0]]+=1
                args=[a for a in segs[i].split()[1:] if not a.startswith('-')]
                for a in args:
                    a=a.strip('"\'')
                    v=re.match(r'"?\$\{?(\w+)',a)
                    if TMP.search(a): k='temp/scratch literal'
                    elif v and v.group(1) in assigns: k='variable assigned on the line -> '+('temp' if TMP.search(assigns[v.group(1)]) else 'non-temp or unknown')
                    elif '$' in a: k='variable not assigned on the line'
                    elif re.match(r'^(/|[A-Za-z]:)',a): k='absolute, not temp'
                    else: k='relative literal'
                    kinds.add(k)
            k=' + '.join(sorted(kinds)) or 'no path argument'
            tgt[k]+=1
            if len(ex[k])<2: ex[k].append(' | '.join(segs[i].strip()[:70] for i in hard[:2]))
print('chain/multi lines analysed:',n)
print('forms:',form.most_common(8))
for k,v in tgt.most_common(12): print('%5d %s'%(v,k)); [print('        e.g.',e) for e in ex[k]]

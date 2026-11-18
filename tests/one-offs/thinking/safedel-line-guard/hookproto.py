import re
SEP = re.compile(r'(\|\||&&|;|\|&?|&|\n)')
WRAP = re.compile(r'^(bash|sh|zsh|eval|xargs|cmd|powershell|pwsh|python\d?|env|sudo|time|nohup)(\.exe)?$', re.I)
RAWVERB = re.compile(r'(?<![\w-])(rm|rmdir|rd|del|erase|unlink|shred|Remove-Item|ri)(?![\w-])|worktree\s+remove|git\b.*\bclean\b|-delete\b|safedel', re.I)
def strip_quotes(s):
    return re.sub(r"""'[^']*'|"[^"]*\"""", 'Q', s)
def destructive(seg, raw_seg):
    t = seg.split()
    while t and re.match(r'^[A-Za-z_]\w*=', t[0]): t = t[1:]
    if not t: return None
    base = re.sub(r'.*[\/]', '', t[0]).lower().removesuffix('.exe'); rest = t[1:]
    if WRAP.match(base) and RAWVERB.search(raw_seg): return 'wrapped:'+base
    if base in ('rm','rmdir','rd','del','erase','unlink','shred','remove-item','ri'): return base
    if base == 'find' and ('-delete' in rest or '-exec' in rest): return 'find'
    if base == 'dz' and rest[:1] == ['safedel'] and rest[1:2] not in (['list'],['status'],['recover']) and '--dry-run' not in rest: return 'dz safedel'
    if base == 'git':
        r = rest[:]
        while r and r[0] in ('-C','-c','--git-dir','--work-tree'): r = r[2:]
        while r and r[0].startswith('--') and '=' in r[0]: r = r[1:]
        j = ' '.join(r)
        if re.match(r'worktree\s+remove|clean\b|reset\s+--hard|branch\s+(-D|-d|--delete)|push\b.*(--force|-f\b)|checkout\s+--|stash\s+(drop|clear)', j): return 'git '+' '.join(r[:2])
    return None
def judge(cmd):
    s = strip_quotes(cmd)
    if s.count("'")%2 or s.count('"')%2 or '<<' in s or '$(' in s or '`' in s:
        return ('BLOCK','unparseable+verb') if RAWVERB.search(cmd) else ('ALLOW','')
    parts = SEP.split(s); rparts = SEP.split(cmd) if len(SEP.split(cmd))==len(parts) else parts
    segs = parts[0::2]; seps = parts[1::2]
    d = [(i, destructive(x, rparts[2*i] if rparts is not parts else x)) for i,x in enumerate(segs)]
    d = [(i,v) for i,v in d if v]
    if not d: return ('ALLOW','')
    if len(d) > 1: return ('BLOCK', 'two destructive steps: %s' % [v for _,v in d])
    i = d[0][0]
    if any(x != '&&' for x in seps[:]) : 
        bad = [x for x in seps if x != '&&']
        return ('BLOCK', 'destructive %r on a line with unchecked %r' % (d[0][1], bad[0]))
    return ('ALLOW','checked chain')
tests = [
 'dz safedel "C:/w/private" 2>&1 | tail -1; ls x; git -C /c/r worktree remove C:/w && git -C /c/r branch -d k',
 'cd /x && dz safedel --yes a',
 'dz safedel --yes a && git worktree prune',
 'git -C /c/r worktree remove C:/w',
 'git -c core.x=1 -C /r clean -fdx',
 'dz safedel --yes a; ls',
 'ls | grep rm',
 'git commit -m "remove rm; use safedel"',
 'bash -c "rm -rf x; echo hi"',
 'dz safedel --dry-run a; echo done',
 'echo $(rm x)',
 'grep -rn "rm" . ; ls',
 'find . -name "*.pyc" -delete',
 'rm a\nrm b',
 'dz safedel list | head',
 'pytest -q 2>&1 | tail -3',
]
for t in tests: print(judge(t), '<=', t.replace('\n','\n')[:90])

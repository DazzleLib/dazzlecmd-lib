"""Scratch prototype: data-table registry + pure judge. Nothing here executes a command."""
import re, sys

# ---- predicates (the only code an entry may reference, by name) ----
def flags(t):  # short-flag clusters expanded, lowercased
    out = set()
    for a in t:
        if a.startswith('--') or a.startswith('/'): out.add(a.lower())
        elif a.startswith('-') and len(a) > 1:
            out.add(a.lower()); out.update('-' + c for c in a[1:].lower())
    return out
def has(*fl):      return lambda t, sh: bool(flags(t) & set(fl))
def psflag(*names):  # PowerShell accepts any unambiguous prefix: -r, -rec, -fo
    def p(t, sh):
        return any(a.startswith('-') and len(a) > 1 and any(n.startswith(a[1:].lower()) for n in names) for a in t)
    return p
def sub(*words):   return lambda t, sh: tuple(x.lower() for x in t[:len(words)]) == words
def allof(*ps):    return lambda t, sh: all(p(t, sh) for p in ps)
def anyof(*ps):    return lambda t, sh: any(p(t, sh) for p in ps)
always = lambda t, sh: True

S, U, D, N = 'self-checking', 'unconditional', 'always-deny', 'not-destructive'
SAFEDEL = 'dz safedel --yes <path>'
# entry: names per shell dialect; forms tried in order, first match wins.
# form: (id, predicate, class, post-condition KIND (data, never run by the judge), message, examples)
REG = [
 dict(tool='rmdir', names={'bash': ('rmdir',), 'cmd': ('rmdir', 'rd')}, forms=[
   ('rmdir.recursive', has('/s', '--ignore-fail-on-non-empty'), U, 'path_absent', 'rd /s deletes a tree. Use: ' + SAFEDEL, ['rd /s /q d']),
   ('rmdir.plain', always, S, None, '', ['rmdir d'])],
   evidence='MSYS rmdir 2026-11-18: refuses non-empty (exit 1) and refuses a junction'),
 dict(tool='rm', names={'bash': ('rm', 'unlink', 'shred')}, forms=[
   ('rm.any', always, U, 'path_absent', 'rm is unrecoverable. Use: ' + SAFEDEL, ['rm -rf x', 'rm x'])]),
 dict(tool='Remove-Item', names={'pwsh': ('remove-item', 'ri', 'rm', 'rmdir', 'rd', 'del', 'erase')}, forms=[
   ('ri.any', always, U, 'path_absent', 'Remove-Item is unrecoverable. Use: ' + SAFEDEL, ['Remove-Item d -r -fo', 'rmdir d'])],
   evidence='pwsh 7.7 2026-11-18: rmdir/rm/del/rd are aliases; -r -fo abbreviations delete a tree'),
 dict(tool='find', names={'bash': ('find',)}, forms=[
   ('find.delete', has('-delete', '-exec', '-execdir', '-ok'), U, 'path_absent', 'List with find, then ' + SAFEDEL, ['find . -name x -delete'])]),
 dict(tool='rsync', names={'bash': ('rsync',)}, forms=[
   ('rsync.delete', lambda t, sh: any(a.startswith('--del') or a == '--remove-source-files' for a in t), U, 'dest_listing', 'rsync --delete removes at the destination. Run with -n first, alone.', ['rsync -a --delete a/ b/'])]),
 dict(tool='robocopy', names={'bash': ('robocopy',), 'pwsh': ('robocopy',), 'cmd': ('robocopy',)}, forms=[
   ('robocopy.purge', has('/mir', '/purge', '/move', '/mov'), U, 'dest_listing', 'robocopy /MIR purges the destination. Run with /L first, alone.', ['robocopy a b /MIR'])]),
 dict(tool='git', names={'bash': ('git',), 'pwsh': ('git',), 'cmd': ('git',)}, pre='git_globals', forms=[
   ('git.worktree.remove', sub('worktree', 'remove'), D, 'junction_targets_intact', 'git worktree remove empties junction targets (G5). Use: dz safedel --yes <worktree>, then: git worktree prune', ['git -C /r worktree remove w']),
   ('git.worktree.prune', sub('worktree', 'prune'), S, None, '', ['git worktree prune']),
   ('git.clean', sub('clean'), D, 'untracked_listing', 'git clean is unrecoverable. List with: git clean -n, then ' + SAFEDEL, ['git -c a=b clean -fdx']),
   ('git.reset.hard', allof(sub('reset'), has('--hard')), U, 'stash_exists', 'Run first, alone: dz git-snapshot save "before reset"', ['git reset --hard HEAD~1']),
   ('git.branch.D', allof(sub('branch'), lambda t, sh: '-D' in t or ('--force' in t and ('-d' in t or '--delete' in t))), U, 'ref_in_reflog', 'Use git branch -d (refuses unmerged work).', ['git branch -D k']),
   ('git.branch.d', allof(sub('branch'), lambda t, sh: '-d' in t or '--delete' in t), S, None, '', ['git branch -d k']),
   ('git.stash.drop', anyof(sub('stash', 'drop'), sub('stash', 'clear')), U, 'stash_listing', 'Run alone and read the dropped hash.', ['git stash clear']),
   ('git.push.force', allof(sub('push'), lambda t, sh: any(a in ('-f', '--force') or a.startswith('+') for a in t)), U, 'remote_ref', 'Use --force-with-lease, alone.', ['git push -f origin main']),
   ('git.checkout.paths', allof(sub('checkout'), lambda t, sh: '--' in t or '.' in t), U, 'diff_saved', 'Snapshot first: dz git-snapshot save', ['git checkout -- .'])]),
 dict(tool='dz safedel', names={'bash': ('dz',), 'pwsh': ('dz',), 'cmd': ('dz',)}, forms=[
   ('safedel.readonly', lambda t, sh: t[:1] == ['safedel'] and (t[1:2] in (['list'], ['status']) or '--dry-run' in t), N, None, '', ['dz safedel list']),
   ('safedel.stage', sub('safedel'), 'staging', 'verdict_line', '', ['dz safedel --yes a'])]),
]
WRAP = {'bash', 'sh', 'zsh', 'eval', 'xargs', 'cmd', 'powershell', 'pwsh', 'python', 'python3', 'py', 'env', 'sudo', 'time', 'nohup', 'start', 'wsl', 'node', 'perl', 'parallel', 'watch', 'timeout'}
SUSPECT = re.compile(r'(?i)(?<![\w-])(rm|rd|rmdir|del|erase|unlink|shred|remove-item|rmtree|os\.remove|os\.unlink|truncate|format|mkfs|dd)(?![\w-])|--?(delete|purge|prune|force|hard)\b|/(mir|purge|s)\b|-delete\b|worktree\s+remove')
SEP = {'bash': r'(\|\||&&|;|\|&?|&|\n)', 'pwsh': r'(\|\||&&|;|\||\n)', 'cmd': r'(\|\||&&|&|\||\n)'}

def git_globals(t):
    while t:
        if t[0] in ('-C', '-c', '--git-dir', '--work-tree', '--namespace'): t = t[2:]
        elif t[0].startswith('-'): t = t[1:]
        else: break
    return t
PRE = {'git_globals': git_globals}

def classify(seg, raw, shell):
    t = seg.split()
    while t and shell == 'bash' and re.match(r'^[A-Za-z_]\w*=', t[0]): t = t[1:]
    if not t: return None
    base = re.sub(r'.*[\\/]', '', t[0]).lower()
    base = re.sub(r'\.(exe|cmd|bat)$', '', base); rest = t[1:]
    if base in WRAP:
        return ('wrapper.' + base, U, None, 'Run the inner command directly, one step per call.') if SUSPECT.search(raw) else None
    for e in REG:
        if base in e['names'].get(shell, ()):
            r = PRE[e['pre']](rest) if e.get('pre') else rest
            for fid, pred, cls, post, msg, _ in e['forms']:
                if pred(r, shell): return (fid, cls, post, msg)
            return None
    return ('unregistered', 'suspect', None, '') if SUSPECT.search(seg) else None

def judge(cmd, shell='bash'):
    s = re.sub(r"""'[^']*'|"[^"]*\"""", 'Q', cmd)
    doubt = s.count("'") % 2 or s.count('"') % 2 or '<<' in s or '$(' in s or (shell == 'bash' and '`' in s) or '@(' in s
    if doubt: return ('DENY', 'unparseable line containing a deletion word') if SUSPECT.search(cmd) else ('ALLOW', '')
    parts = re.split(SEP[shell], s); rp = re.split(SEP[shell], cmd)
    if len(rp) != len(parts): rp = parts
    segs, seps = parts[0::2], parts[1::2]
    c = [classify(x, rp[2 * i], shell) for i, x in enumerate(segs)]
    log = [x[0] for x in c if x and x[1] == 'suspect']
    for x in c:
        if x and x[1] == D: return ('DENY', x[3])
    hard = [(i, x) for i, x in enumerate(c) if x and x[1] in (U, 'staging')]
    soft = [(i, x) for i, x in enumerate(c) if x and x[1] == S]
    if len(hard) > 1: return ('DENY', 'more than one deletion on the line (%s): run each alone and read its result' % ', '.join(x[0] for _, x in hard))
    if hard:
        i, x = hard[0]
        if x[1] == U and (len(segs) > 1): return ('DENY', x[3] + ' [unconditional form; must be the only command in the call]')
        if any(sp != '&&' for sp in seps): return ('DENY', 'a deletion shares the line with %r; only && may join it' % [sp for sp in seps if sp != '&&'][0])
        if x[1] == U: return ('ALLOW-POST', x[2])
    if any(sp != '&&' for j, sp in enumerate(seps) for i, _ in soft if j == i - 1) and hard:
        return ('DENY', 'self-checking step must follow via &&')
    return ('ALLOW', 'log:' + ','.join(log) if log else '')

TESTS = [  # (shell, line, expected verdict)
 ('bash', 'dz safedel --yes d/* && rmdir d', 'ALLOW'),
 ('bash', 'dz safedel --yes x && git worktree prune', 'ALLOW'),
 ('bash', 'dz safedel x; rm -rf x', 'DENY'),
 ('bash', 'rm -rf a && rm -rf b', 'DENY'),
 ('bash', 'dz safedel --yes d/* ; rmdir d', 'DENY'),
 ('bash', 'rmdir a && rmdir b', 'ALLOW'),
 ('bash', 'rmdir a; rmdir b', 'ALLOW'),
 ('bash', 'rm -rf x', 'ALLOW-POST'),
 ('bash', 'cd /x && rm -rf build', 'DENY'),
 ('bash', 'git -C /c/r worktree remove C:/w', 'DENY'),
 ('bash', 'git -c core.x=1 -C /r clean -fdx', 'DENY'),
 ('bash', 'git --no-pager -C /r worktree remove w', 'DENY'),
 ('bash', 'bash -c "rm -rf x; echo hi"', 'DENY'),
 ('bash', 'python -c "import shutil; shutil.rmtree(\'x\')"', 'DENY'),
 ('bash', 'git commit -m "remove rm; use safedel"', 'ALLOW'),
 ('bash', 'ls | grep rm', 'ALLOW'),
 ('bash', 'grep -rn "rm" . ; ls', 'ALLOW'),
 ('bash', 'rd /s /q d', 'DENY'),          # cmd form typed into bash: not a bash name -> ?
 ('cmd',  'rd /s /q d', 'ALLOW-POST'),
 ('pwsh', 'rmdir d', 'ALLOW-POST'),        # alias of Remove-Item: NOT the self-checking rmdir
 ('pwsh', 'dz safedel --yes d/* && rmdir d -r -fo', 'DENY'),
 ('pwsh', 'Remove-Item a -Recurse -Force; Remove-Item b -Recurse -Force', 'DENY'),
 ('bash', 'rsync -a --delete a/ b/ && echo ok', 'DENY'),
 ('bash', 'robocopy a b /MIR', 'ALLOW-POST'),
 ('bash', 'find . -name "*.pyc" | xargs rm', 'DENY'),
 ('bash', 'git branch -d k && git branch -d j', 'ALLOW'),
 ('bash', 'git branch -D k', 'ALLOW-POST'),
 ('bash', 'truncate -s 0 important.db', 'ALLOW'),   # unregistered: logged, not blocked
 ('bash', 'mv a /dev/null', 'ALLOW'),
 ('bash', 'cp /dev/null important.db', 'ALLOW'),
 ('bash', ': > important.db', 'ALLOW'),
 ('bash', 'git worktree prune; rm -rf x', 'DENY'),
 ('bash', 'cmake --build . --target clean && rmdir out', 'ALLOW'),
 ('bash', 'npm run clean', 'ALLOW'),
]
if __name__ == '__main__':
    bad = 0
    for sh, line, want in TESTS:
        got = judge(line, sh)
        ok = got[0] == want; bad += not ok
        print('%-4s %-10s %-4s %s   <= %s' % ('ok' if ok else 'MISS', got[0], sh, got[1][:70] if got[1] else '', line))
    # 1) every form carries an example that classifies to ITSELF (shadowing check)
    for e in REG:
        sh = next(iter(e['names']))
        for fid, pred, cls, post, msg, ex in e['forms']:
            for line in ex:
                got = classify(line, line, sh)
                if not got or got[0] != fid: bad += 1; print('SHADOWED', fid, '<=', line, '->', got)
            if cls in (U, D) and not msg: bad += 1; print('NO MESSAGE', fid)
            if cls == U and not post: bad += 1; print('NO POSTCONDITION', fid)
    # 2) every message's "Use: <cmd>" alternative must itself be allowed by the judge
    for e in REG:
        for fid, pred, cls, post, msg, ex in e['forms']:
            for alt in re.findall(r'(dz safedel --yes <\w+>|git worktree prune|git clean -n|git branch -d)', msg):
                v = judge(alt.replace('<', '').replace('>', ''), 'bash')
                if v[0] == 'DENY': bad += 1; print('MESSAGE RECOMMENDS A DENIED COMMAND', fid, alt, v)
    print('failures:', bad)

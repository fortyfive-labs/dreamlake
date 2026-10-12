"""Bounded Unicode-codepoint alignment; no transport or coarse fallback."""
from array import array

MAX_SOURCE = 1_000_000
MAX_WORK = 4_000_000
MAX_TRACE = 250_000
MAX_EDITS = 10_000

class NotePatchError(ValueError):
    def __init__(self, message, code='INVALID_PATCH'):
        super().__init__(message)
        self.code = code

def limit(condition, message):
    if condition:
        raise NotePatchError(message, 'LIMIT_EXCEEDED')

def points(text):
    if not isinstance(text, str):
        raise NotePatchError('Source must be a string')
    limit(len(text) > MAX_SOURCE, 'Source too large')
    if any(0xD800 <= ord(c) <= 0xDFFF for c in text):
        raise NotePatchError('Unpaired Unicode surrogate')
    return text

def collect(a, b, steps, offset):
    edits, active = [], None
    i = j = 0
    for step in steps:
        if step == 'equal':
            if active is not None:
                active['expectedText'] = ''.join(active['expectedText'])
                active['replacementText'] = ''.join(active['replacementText'])
                edits.append(active); active = None
            i += 1; j += 1
            continue
        if active is None:
            active = dict(start=offset+i, end=offset+i, expectedText=[], replacementText=[])
        if step == 'delete':
            active['expectedText'].append(a[i]); active['end'] += 1; i += 1
        else:
            active['replacementText'].append(b[j]); j += 1
    if active is not None:
        active['expectedText'] = ''.join(active['expectedText'])
        active['replacementText'] = ''.join(active['replacementText'])
        edits.append(active)
    limit(len(edits) > MAX_EDITS, 'Too many edits')
    return edits

def character_diff(base, target, offset=0, budget=None):
    a, b = points(base), points(target)
    budget = [0] if budget is None else budget
    prefix = 0
    while prefix < min(len(a), len(b)) and a[prefix] == b[prefix]:
        prefix += 1
    ae, be = len(a), len(b)
    while ae > prefix and be > prefix and a[ae-1] == b[be-1]:
        ae -= 1; be -= 1
    a, b, offset = a[prefix:ae], b[prefix:be], offset+prefix
    n, m = len(a), len(b)
    if not n and not m:
        return []
    if not n or not m:
        return [dict(start=offset, end=offset+n, expectedText=a, replacementText=b)]
    def spend(cells):
        budget[0] += cells
        limit(budget[0] > MAX_WORK, 'Character alignment work limit exceeded')
    if (n+1)*(m+1) > MAX_WORK-budget[0]:
        try:
            return sparse(a, b, offset, spend)
        except NotePatchError as e:
            if str(e) != 'Character alignment trace limit exceeded':
                raise
            return packed(a, b, offset, spend)
    spend((n+1)*(m+1))
    width = m+1
    table = array('I', [0])*((n+1)*width)
    for i in range(n-1, -1, -1):
        for j in range(m-1, -1, -1):
            table[i*width+j] = 1+table[(i+1)*width+j+1] if a[i] == b[j] else max(table[(i+1)*width+j],table[i*width+j+1])
    def steps():
        i = j = 0
        while i < n or j < m:
            if i < n and j < m and a[i] == b[j]:
                yield 'equal'; i += 1; j += 1
            elif i < n and (j == m or table[(i+1)*width+j] >= table[i*width+j+1]):
                yield 'delete'; i += 1
            else:
                yield 'insert'; j += 1
    return collect(a,b,steps(),offset)

def sparse(a,b,offset,spend):
    frontier, trace, trace_cells = {1:0}, [], 0
    for distance in range(len(a)+len(b)+1):
        trace_cells += len(frontier)
        limit(trace_cells > MAX_TRACE, 'Character alignment trace limit exceeded')
        trace.append(frontier.copy())
        for k in range(-distance,distance+1,2):
            spend(1)
            left,right = frontier.get(k-1,float('-inf')),frontier.get(k+1,float('-inf'))
            x = right if k == -distance or (k != distance and left < right) else left+1
            y = x-k
            while x < len(a) and y < len(b) and a[x] == b[y]:
                spend(1); x += 1; y += 1
            frontier[k] = x
            if x < len(a) or y < len(b):
                continue
            reversed_steps=[]
            for d in range(distance,-1,-1):
                prior,k=trace[d],x-y
                pk=k+1 if k == -d or (k != d and prior.get(k-1,float('-inf')) < prior.get(k+1,float('-inf'))) else k-1
                px=prior.get(pk,0); py=px-pk
                while x > px and y > py:
                    reversed_steps.append('equal'); x -= 1; y -= 1
                if d:
                    if x == px:
                        reversed_steps.append('insert'); y -= 1
                    else:
                        reversed_steps.append('delete'); x -= 1
            return collect(a,b,reversed(reversed_steps),offset)
    raise NotePatchError('Character alignment failed')

def packed(a,b,offset,spend):
    words=(len(b)+63)//64
    limit((len(a)+1+len(set(b))+10)*words > MAX_TRACE,'Character alignment trace limit exceeded')
    masks={}
    for j,c in enumerate(b):
        spend(words*2)
        masks[c]=masks.get(c,0)|(1<<(len(b)-1-j))
    rows=[0]*(len(a)+1)
    for i in range(len(a)-1,-1,-1):
        spend(words*7)
        previous=rows[i+1]; matches=previous|masks.get(a[i],0)
        rows[i]=matches&~(matches-((previous<<1)|1))
    def rank(row,length):
        spend(((length+63)//64)*3)
        # Match the proposal's bounded hex-chunk accounting; no bitwise scan.
        value=row&((1<<length)-1)
        spend(max(1,(value.bit_length()+31)//32))
        return value.bit_count()
    def steps():
        i=j=0; remaining=rank(rows[0],len(b))
        while i < len(a) or j < len(b):
            spend(1)
            if i < len(a) and j < len(b) and a[i] == b[j]:
                yield 'equal'; i += 1; j += 1; remaining -= 1
            elif i < len(a) and (j == len(b) or rank(rows[i+1],len(b)-j) == remaining):
                yield 'delete'; i += 1
            else:
                yield 'insert'; j += 1
    return collect(a,b,steps(),offset)

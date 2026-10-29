from __future__ import annotations

from dataclasses import dataclass, asdict
import json
from pathlib import Path
import re
import time
import unicodedata
from typing import Iterable

from rapidfuzz import fuzz, process

STOPWORDS = {
    'a','o','as','os','um','uma','de','da','do','das','dos','em','na','no','nas','nos',
    'para','por','e','que','me','minha','meu','favor','porfavor','pra','pro','ao','à','com'
}
ACTIONS = {
    'liga','ligue','ligar','acenda','acende','acender','desliga','desligue','desligar','apaga','apague','apagar',
    'abre','abra','abrir','fecha','feche','fechar','trava','trave','trancar','destrava','destrave','destrancar',
    'aumenta','aumente','diminui','diminua','coloca','coloque','ajusta','ajuste','define','defina','muda','mude',
    'qual','quanto','quanta','mostra','mostre','diz','diga','inicia','inicie','para','pare','pausa','pause','retoma','retome'
}
VOICE_DOMAINS = {
    'light','fan','climate','media_player','switch','cover','lock','vacuum','scene','script','button',
    'sensor','binary_sensor','person','input_boolean','input_button','select','number','timer','camera','remote'
}
TECHNICAL_SUFFIXES = {
    'update','battery','bateria','voltage','tensao','firmware','rssi','lqi','linkquality','diagnostic','config',
    'power on behavior','do not disturb','current temperature','current status','state','status'
}


def fold(s: str) -> str:
    s = unicodedata.normalize('NFD', str(s).casefold())
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    s = s.replace('-', ' ')
    s = re.sub(r'[^a-z0-9+ ]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def words(s: str) -> list[str]:
    return fold(s).split()


def phonetic(s: str) -> str:
    s = fold(s)
    s = re.sub(r'nh', 'N', s); s = re.sub(r'lh', 'L', s); s = re.sub(r'ch', 'X', s)
    s = re.sub(r'(rr|ss|sc|sç|xc)', 'S', s)
    s = re.sub(r'qu(?=[ei])', 'k', s); s = re.sub(r'gu(?=[ei])', 'g', s)
    s = re.sub(r'c(?=[ei])', 's', s); s = s.replace('c','k')
    s = re.sub(r'g(?=[ei])', 'j', s); s = s.replace('h','')
    s = s.replace('y','i').replace('w','v')
    s = re.sub(r'[aeiou]+', 'A', s)
    s = re.sub(r'[^A-Za-z0-9]+', '', s)
    s = re.sub(r'(.)\1+', r'\1', s)
    return s.lower()


def prettify_basic(text: str) -> str:
    out = re.sub(r'\s+', ' ', text).strip()
    # Common ASR artifact around the Portuguese article/preposition sequence: "aà luz".
    # Normalize only the duplicated article form; do not globally rewrite valid grave-accent uses.
    out = re.sub(r'(?i)\ba\s*à\s+(?=(?:luz|lâmpada|lampada|luminária|luminaria)\b)', 'a ', out)
    out = re.sub(r'\b(?:te\s+ve|tev[eê]?)\b', 'TV', out, flags=re.I)
    out = re.sub(r'\bar\s*[- ]?\s*condicionado\b', 'ar-condicionado', out, flags=re.I)
    out = re.sub(r'\bmicro\s*[- ]?\s*ondas\b', 'micro-ondas', out, flags=re.I)
    out = re.sub(r'\blava\s*[- ]?\s*lou[cç]as?\b', 'lava-louças', out, flags=re.I)
    out = re.sub(r'\bmaquina\s+de\s+lavar\b', 'máquina de lavar', out, flags=re.I)
    return out


def is_natural_name(s: str | None) -> bool:
    if not s: return False
    f = fold(s)
    if len(f) < 2 or len(f) > 80: return False
    if re.fullmatch(r'[0-9.]+', f): return False
    if any(x in f for x in TECHNICAL_SUFFIXES) and len(f.split()) > 3: return False
    return True


@dataclass(frozen=True)
class LexEntry:
    phrase: str
    canonical: str
    source: str
    priority: float = 1.0
    entity_ids: tuple[str, ...] = ()


@dataclass
class Change:
    before: str
    after: str
    layer: str
    score: float | None = None
    source: str | None = None
    entity_ids: list[str] | None = None


class AranduLexicalResolver:
    def __init__(self, inventory_path: Path, resources_dir: Path, inventory: dict | None = None):
        self.resources_dir = resources_dir
        self.inventory = inventory if inventory is not None else json.loads(inventory_path.read_text(encoding='utf-8'))
        self.confusions = json.loads((resources_dir/'asr_confusions.json').read_text(encoding='utf-8'))
        self.common_names = [x.strip() for x in (resources_dir/'common_names_ptbr.txt').read_text(encoding='utf-8').splitlines() if x.strip()]
        self.device_types = [x.strip() for x in (resources_dir/'home_device_types_ptbr.txt').read_text(encoding='utf-8').splitlines() if x.strip()]
        self.areas = [a['name'] for a in self.inventory.get('areas',[]) if a.get('name')]
        self.area_fold = {fold(x):x for x in self.areas}
        self.light_areas = {fold(e.get('area')) for e in self.inventory.get('entities',[]) if e.get('domain')=='light' and e.get('area') and not e.get('disabled')}
        # Canonical light names allow narrowly-scoped corrections such as ASR "dor de <pessoa>" -> "Luz de <pessoa>".
        # The bad token is never replaced globally; a real light entity must exist with the same qualifier.
        self.light_name_targets = []
        for e in self.inventory.get('entities', []):
            if e.get('disabled') or e.get('hidden') or e.get('domain') != 'light':
                continue
            name = str(e.get('name') or '')
            m = re.match(r'(?i)^luz\s+(.+)$', name.strip())
            if m:
                self.light_name_targets.append((m.group(1).strip(), name.strip(), e.get('entity_id')))
        # Areas that really contain a TV/media target. Used only for context-scoped ASR corrections.
        self.tv_areas = set()
        for e in self.inventory.get('entities', []):
            if e.get('disabled') or not e.get('area') or e.get('domain') not in {'media_player','remote','switch'}:
                continue
            blob = fold(' '.join(str(e.get(k) or '') for k in ('name','device','manufacturer','model')))
            if 'tv' in blob or 'televis' in blob or 'samsung' in blob:
                self.tv_areas.add(fold(e.get('area')))
        self.entries = self._build_entries()
        self._candidate_index = {}
        self._exact_index = {}
        for e in self.entries:
            if e.priority < 1.5: continue
            f=fold(e.phrase); n=len(words(e.phrase)); item=(e,f,phonetic(f),n)
            self._candidate_index.setdefault(n,[]).append(item)
            old=self._exact_index.get(f)
            if old is None or e.priority>old[0].priority:
                self._exact_index[f]=item
        self.generic_terms = self._dedupe_terms(self.device_types + self.common_names + self.areas)
        # Precomputed structures used by the generalized rescue on every utterance. Keeping them
        # out of apply() preserves the sub-10ms correction budget even with 1k+ HA entities.
        self._real_names=[]
        for e in self.inventory.get('entities',[]):
            if e.get('disabled') or e.get('hidden') or e.get('domain') not in VOICE_DOMAINS:
                continue
            name=str(e.get('name') or '').strip()
            if name:
                self._real_names.append((fold(name),name,e.get('entity_id'),e.get('domain'),fold(e.get('area') or '')))
        self._lava_lamp=next(((name,eid) for f,name,eid,dom,area in self._real_names if f=='lava lamp'),None)
        self._single_areas=[(fold(a),a) for a in self.areas if len(words(a))==1]
        light_terms={'luz','lampada','luminaria','abajur','lustre','plafon','led','iluminacao','fita led'}
        self._device_term_meta=[]
        for term in self.device_types:
            tf=fold(term)
            if tf and len(tf)>=3:
                self._device_term_meta.append((tf, tf in light_terms))

        # Inventory-derived targets owned/named after a real HA person.  This enables a final,
        # context-gated cleanup for clipped proper names such as "ventilador já" or
        # "ventilador de Jie" without ever rewriting "já" globally.
        people=[str(p.get('name') or '').strip() for p in self.inventory.get('persons',[]) if p.get('name')]
        self._owned_targets=[]
        seen_owned=set()
        for e in self.inventory.get('entities',[]):
            if e.get('disabled') or e.get('hidden') or e.get('domain') not in VOICE_DOMAINS:
                continue
            name=str(e.get('name') or '').strip()
            if not name:
                continue
            for person in people:
                m=re.match(r'(?i)^(.+?)\s+(?:de|do|da)\s+'+re.escape(person)+r'$',name)
                if not m:
                    continue
                base=m.group(1).strip()
                key=(fold(base),fold(person),name,e.get('entity_id'))
                if key in seen_owned:
                    continue
                seen_owned.add(key)
                self._owned_targets.append((base,person,name,e.get('entity_id')))

        # Canonical voice targets used by the final de-duplication pass.  We only consider
        # real HA entity names (plus a few generated lexical entries with entity IDs), so
        # ordinary repeated words in free speech are never collapsed.
        dedup={}
        for e in self.entries:
            if not e.entity_ids or not is_natural_name(e.canonical):
                continue
            f=fold(e.canonical)
            if len(f)>=3:
                dedup.setdefault(f,e.canonical)
        self._dedup_targets=sorted(dedup.values(), key=lambda x:(-len(words(x)),-len(x)))

    @staticmethod
    def _dedupe_terms(items: Iterable[str]) -> list[str]:
        seen={};
        for x in items:
            f=fold(x)
            if f and f not in seen: seen[f]=x
        return list(seen.values())

    def _build_entries(self) -> list[LexEntry]:
        entries=[]
        def add(phrase, canonical=None, source='inventory', priority=1.0, entity_ids=()):
            if not is_natural_name(phrase): return
            canonical = canonical or phrase
            entries.append(LexEntry(str(phrase).strip(), str(canonical).strip(), source, priority, tuple(entity_ids)))
        for p in self.inventory.get('persons',[]):
            add(p.get('name'), p.get('name'), 'person', 3.0, [p.get('entity_id')] if p.get('entity_id') else [])
            for a in p.get('aliases') or []: add(a,p.get('name'),'person_alias',3.1,[p.get('entity_id')])
        for a in self.inventory.get('areas',[]):
            add(a.get('name'),a.get('name'),'area',2.6)
            for al in a.get('aliases') or []: add(al,a.get('name'),'area_alias',2.7)
        for d in self.inventory.get('devices',[]):
            canonical=d.get('name_by_user') or d.get('name')
            if canonical:
                add(canonical,canonical,'device_user' if d.get('name_by_user') else 'device',2.1)
                # Original spoken name remains itself: correction must not invent qualifiers the user did not say.
                if d.get('name') and d.get('name')!=canonical: add(d.get('name'),d.get('name'),'device_original',1.9)
            for al in d.get('aliases') or []: add(al,canonical or al,'device_alias',2.2)
        area_domains={}
        for e in self.inventory.get('entities',[]):
            if e.get('disabled') or e.get('hidden') or e.get('domain') not in VOICE_DOMAINS: continue
            canonical=e.get('name') or e.get('original_name')
            eid=e.get('entity_id')
            pri=2.7 if e.get('exposed_to_assist') else 1.65
            if e.get('entity_category') in ('config','diagnostic'): pri=0.7
            add(canonical,canonical,'entity',pri,[eid] if eid else [])
            if e.get('original_name') and e.get('original_name')!=canonical: add(e.get('original_name'),canonical,'entity_original',max(.6,pri-.4),[eid])
            for al in (e.get('aliases') or [])+(e.get('assist_aliases') or []): add(al,canonical or al,'entity_alias',pri+.2,[eid])
            if e.get('area'):
                area_domains.setdefault((e.get('domain'),e.get('area')),[]).append(eid)
        # Generic spoken category + area aliases. They improve scalability without inventing target IDs.
        domain_terms={
            'light':['luz','lâmpada','iluminação'], 'fan':['ventilador'], 'climate':['ar-condicionado','ar condicionado'],
            'vacuum':['aspirador','robô aspirador'], 'lock':['fechadura'], 'cover':['cortina','persiana'],
        }
        for (domain,area),ids in area_domains.items():
            for term in domain_terms.get(domain,[]):
                prep='da' if fold(area) in {'sala','cozinha','area de servico'} else 'do'
                if fold(area).startswith('escritorio') or fold(area).startswith('banheiro'): prep='do'
                add(f'{term} {prep} {area}',f'{term} {prep} {area}','generated_domain_area',2.45,ids)
        # Common media aliases only when the real inventory indicates a matching media device in that area.
        for area in self.areas:
            ids=[]
            for e in self.inventory.get('entities',[]):
                if e.get('domain')!='media_player' or e.get('area')!=area or e.get('disabled'): continue
                blob=fold(' '.join(str(e.get(k) or '') for k in ('name','device','manufacturer','model')))
                if 'tv' in blob or 'samsung' in blob or 'televis' in blob: ids.append(e.get('entity_id'))
            if ids:
                prep='da' if fold(area) in {'sala','cozinha','area de servico'} else 'do'
                add(f'TV {prep} {area}',f'TV {prep} {area}','generated_media_area',2.5,ids)
                add(f'televisão {prep} {area}',f'TV {prep} {area}','generated_media_area',2.4,ids)
        # Common types and names are vocabulary only, never bound to an entity here.
        for t in self.device_types: add(t,t,'generic_device',0.75)
        for n in self.common_names: add(n,n,'generic_name',0.55)
        # Deduplicate by folded phrase+canonical, keeping highest priority and union ids.
        merged={}
        for e in entries:
            k=(fold(e.phrase),fold(e.canonical))
            if not k[0]: continue
            old=merged.get(k)
            if old is None:
                merged[k]=e
            else:
                ids=tuple(dict.fromkeys(old.entity_ids+e.entity_ids))
                merged[k]=LexEntry(old.phrase, old.canonical, old.source if old.priority>=e.priority else e.source, max(old.priority,e.priority), ids)
        return list(merged.values())

    def normalize_only(self,text:str):
        out=prettify_basic(text)
        changes=[]
        if out!=text: changes.append(Change(text,out,'normalize'))
        return out,changes

    def acoustic_only(self,text:str):
        out=text; changes=[]
        for bad,good in sorted(self.confusions.get('phrases',{}).items(), key=lambda x:-len(x[0])):
            pat=re.compile(r'(?<!\w)'+re.escape(bad)+r'(?!\w)',re.I)
            new,n=pat.subn(good,out)
            if n: changes.append(Change(bad,good,'acoustic_alias')); out=new
        for bad,good in self.confusions.get('words',{}).items():
            pat=re.compile(r'(?<!\w)'+re.escape(bad)+r'(?!\w)',re.I)
            new,n=pat.subn(good,out)
            if n: changes.append(Change(bad,good,'acoustic_alias')); out=new
        # Observed FastConformer confusion: spoken "luz" can become "dor".
        # Replace only when the remaining qualifier identifies a real light entity from this HA inventory.
        for qualifier, canonical, eid in self.light_name_targets:
            qpat=r'\s+'.join(re.escape(x) for x in qualifier.split())
            pat=re.compile(r'\bdor\s+'+qpat+r'\b', re.I)
            new,n=pat.subn(canonical,out)
            if n:
                changes.append(Change('dor '+qualifier,canonical,'contextual_acoustic',entity_ids=[eid] if eid else None))
                out=new

        # FastConformer recurrent light confusion, but only when followed by a real HA area containing a light.
        for af,area in self.area_fold.items():
            if af not in self.light_areas: continue
            # nos/luis + preposition + area => light phrase. Scoped so a person called Luis elsewhere isn't globally changed.
            area_pat=re.escape(area)
            pat=re.compile(r'\b(?:nos|lu[ií]s)\s+(?:de|do|da)\s+'+area_pat+r'\b',re.I)
            prep='da' if af in {'sala','cozinha','area de servico'} else 'do'
            replacement=f'luz {prep} {area.casefold()}'
            new,n=pat.subn(replacement,out)
            if n: changes.append(Change('nos/Luís + área',replacement,'contextual_acoustic')); out=new
        # Observed FastConformer confusion for spoken "TV": feer/Rfeer/feier/fir.
        # Never replace globally: only when followed by a real HA area that contains a TV target.
        for af,area in self.area_fold.items():
            if af not in self.tv_areas: continue
            area_pat=re.escape(area)
            pat=re.compile(r'\b(?:r?feer|feier|fier|fir)\s+(?:de|do|da)\s+'+area_pat+r'\b', re.I)
            prep='da' if af in {'sala','cozinha','area de servico'} else 'do'
            replacement=f'TV {prep} {area.casefold()}'
            new,n=pat.subn(replacement,out)
            if n:
                changes.append(Change('feer/fir + área',replacement,'contextual_acoustic'))
                out=new
        return out,changes

    def command_rescue(self, text: str):
        """Recover heavily mangled home-command fragments seen in real FastConformer output.

        This layer is deliberately contextual. It does not globally rewrite short/common words.
        It only activates around a fan-like target and a known HA person, or for a standalone
        person fragment. This lets us recover severe forms such as
        um comando de ventilador severamente mutilado without turning arbitrary
        free speech into Home Assistant commands.
        """
        changes=[]
        out=text
        person_names={fold(p.get('name')):p.get('name') for p in self.inventory.get('persons',[]) if p.get('name')}
        person_fragments={fold(k):v for k,v in self.confusions.get('person_fragments',{}).items()}
        action_fragments={fold(k):v for k,v in self.confusions.get('action_fragments',{}).items()}
        fan_fragments=sorted((fold(x),x) for x in self.confusions.get('fan_fragments',[]) if fold(x))

        # A single severely truncated proper name can still be useful to the later dialogue/NLU layer,
        # but never invent an action/device from it.
        stripped=fold(out)
        if len(words(out))==1 and stripped in person_fragments:
            canonical=person_fragments[stripped]
            if fold(canonical) in person_names:
                changes.append(Change(out,canonical,'command_rescue_person',88.0,'observed_context'))
                return canonical,changes

        parts=re.split(r'([,;.!?]+)',out)
        for pi in range(0,len(parts),2):
            clause=parts[pi]
            cf=fold(clause)
            if not cf:
                continue

            # Fan context can be explicit or one of the severe recurrent fragments learned from logs.
            matched_fragment=None
            if re.search(r'\bventilador(?:es)?\b',cf):
                fan_context=True
            else:
                fan_context=False
                for frag,_orig in fan_fragments:
                    if re.search(r'(?<!\w)'+re.escape(frag)+r'(?!\w)',cf):
                        matched_fragment=frag
                        fan_context=True
                        break
            if not fan_context:
                continue

            # Resolve very short person fragments only inside this strong device context.
            for bad,canonical in person_fragments.items():
                if fold(canonical) not in person_names:
                    continue
                pat=re.compile(r'(?<!\w)'+re.escape(bad)+r'(?!\w)',re.I)
                new,n=pat.subn(canonical,clause)
                if n:
                    changes.append(Change(bad,canonical,'command_rescue_person',92.0,'fan_context'))
                    clause=new

            # Replace severe fan fragments only here. Ordinary acoustic aliases are handled earlier.
            for frag,orig in fan_fragments:
                pat=re.compile(r'(?<!\w)'+re.escape(orig)+r'(?!\w)',re.I)
                new,n=pat.subn('ventilador',clause)
                if n:
                    changes.append(Change(orig,'ventilador','command_rescue_target',90.0,'fan_context'))
                    clause=new

            # Recover action only at the beginning of a clause and only after fan context was established.
            for bad,canonical in action_fragments.items():
                pat=re.compile(r'^(\s*)'+re.escape(bad)+r'\b',re.I)
                new,n=pat.subn(lambda m:m.group(1)+canonical,clause)
                if n:
                    changes.append(Change(bad,canonical,'command_rescue_action',90.0,'fan_context'))
                    clause=new
                    break

            # If the ASR dropped the connector, restore it only for a real HA person name.
            for pf,canonical in person_names.items():
                pat=re.compile(r'\bventilador\s+'+re.escape(canonical)+r'\b',re.I)
                new,n=pat.subn('ventilador de '+canonical,clause)
                if n:
                    changes.append(Change('ventilador '+canonical,'ventilador de '+canonical,'command_rescue_grammar',96.0,'fan_context'))
                    clause=new

            parts[pi]=clause
        return ''.join(parts),changes

    def generalized_command_rescue(self, text: str):
        """Generalize recovery beyond one-off ASR substitutions.

        The recovery is target-gated: we infer a damaged action only when the clause still
        contains a strong home-automation target signal (real inventory term, room, or a
        high-confidence mixed-language target such as Lava Lamp). This allows broad families
        such as L* -> liga, D* -> desliga, AC/AS* -> acende and AP* -> apaga without turning
        arbitrary free speech into commands.
        """
        changes=[]
        out=text

        # Precomputed inventory structures keep this hot path cheap.
        real_names=self._real_names
        has_lava_lamp=self._lava_lamp
        single_areas=self._single_areas

        # Fuzzy room correction first. This is deliberately conservative and only operates on
        # words adjacent to a preposition or at the end of a short command-like clause.
        parts=re.split(r'([,;.!?]+)',out)
        for pi in range(0,len(parts),2):
            clause=parts[pi]
            if not fold(clause):
                continue
            toks=list(re.finditer(r"[\wÀ-ÿ+-]+",clause,flags=re.UNICODE))
            for i,m in enumerate(toks):
                ft=fold(m.group(0))
                if len(ft)<4:
                    continue
                best=None
                for af,area in single_areas:
                    ch=fuzz.ratio(ft,af); ph=fuzz.ratio(phonetic(ft),phonetic(af))
                    score=.55*ch+.45*ph
                    if score>=78 and (best is None or score>best[0]): best=(score,area)
                if not best or fold(best[1])==ft:
                    continue
                prev=fold(toks[i-1].group(0)) if i else ''
                is_tail=(i==len(toks)-1)
                # Room typos are most trustworthy after de/do/da or as the final token of a short command.
                first_fold=fold(toks[0].group(0)) if toks else ''
                commandish_tail = is_tail and len(toks)<=5 and (
                    first_fold in ACTIONS or
                    any(fold(x.group(0)) in {'luz','nos','noz','tv','teve','tev','ventilador','luminaria','lampada'} for x in toks[:-1])
                )
                if prev not in {'de','do','da','no','na'} and not commandish_tail:
                    continue
                before=m.group(0); after=best[1]
                clause=clause[:m.start()]+after+clause[m.end():]
                changes.append(Change(before,after,'general_area_fuzzy',round(best[0],1),'area'))
                break

            cf=fold(clause)

            # Canonicalize Lava Lamp using a robust anchor rather than enumerating every misspelling.
            # Never consume a following action token. Replacements are collected once and applied
            # right-to-left, which is both safer and much cheaper than repeated regex passes.
            if has_lava_lamp and 'lava louc' not in cf:
                ms=list(re.finditer(r"[\wÀ-ÿ+-]+",clause,flags=re.UNICODE))
                fws=[fold(m.group(0)) for m in ms]
                repls=[]; i=0
                while i < len(fws):
                    w=fws[i]
                    if w in {'lavadora','lavar','lavagem','lavanderia','lava-loucas','lavaloucas'}:
                        i+=1; continue
                    if w.startswith('lav') and len(w)>4:
                        repls.append((ms[i].start(),ms[i].end(),clause[ms[i].start():ms[i].end()],96.0)); i+=1; continue
                    if i+1 < len(fws):
                        nxt=fws[i+1]
                        if nxt not in ACTIONS and nxt not in {'e','ou','mas'}:
                            lava_like=w.startswith('lav') or fuzz.ratio(w,'lava')>=75
                            lamp_like=nxt.startswith(('lam','lem','len','lpe')) or fuzz.ratio(nxt,'lamp')>=58
                            if lava_like and lamp_like:
                                span=clause[ms[i].start():ms[i+1].end()]
                                if fold(span)!='lava lamp':
                                    repls.append((ms[i].start(),ms[i+1].end(),span,95.0))
                                i+=2; continue
                    i+=1
                for rs,re_,before,sc in reversed(repls):
                    clause=clause[:rs]+'Lava Lamp'+clause[re_:]
                    changes.append(Change(before,'Lava Lamp','general_target_anchor',sc,'inventory',[has_lava_lamp[1]] if has_lava_lamp[1] else None))
                if repls:
                    cf=fold(clause)

            # Light target recovery: a known room + a badly recognized short token such as "nos".
            # The rule is inventory-gated; the area must actually contain a light.
            for af,area in self.area_fold.items():
                if af not in self.light_areas:
                    continue
                if af not in cf:
                    continue
                # optional preposition handles "nos quarto" as well as "nos do quarto".
                area_pat=re.escape(area)
                pat=re.compile(r'(?i)\b(?:nos|noz|lu[ií]s)\s+(?:(?:de|do|da)\s+)?'+area_pat+r'\b')
                prep='da' if af in {'sala','cozinha','area de servico'} else 'do'
                repl=f'luz {prep} {area.casefold()}'
                new,n=pat.subn(repl,clause)
                if n:
                    changes.append(Change('light-like fragment + area',repl,'general_target_context',94.0,'inventory'))
                    clause=new
                    cf=fold(clause)

            # Decide whether we have enough target evidence to infer a mangled action.
            # Exact/generic target words, room names, or the canonicalized Lava Lamp are strong signals.
            strong=False
            light_context=False
            cwords=set(words(clause))
            if 'lava lamp' in cf:
                strong=True; light_context=True
            for tf,is_light in self._device_term_meta:
                if tf in cf or (' ' not in tf and tf in cwords):
                    strong=True
                    if is_light:
                        light_context=True
                    break
            if any(af and re.search(r'(?<!\w)'+re.escape(af)+r'(?!\w)',cf) for af in self.area_fold):
                strong=True
            # Real inventory names are stronger than the generic list.
            if not strong:
                for nf,name,eid,dom,area in real_names:
                    if len(nf)>=4 and nf in cf:
                        strong=True; light_context = light_context or dom=='light'; break

            if light_context:
                new,n=re.subn(r'(?i)\bà\s+(?=(?:luz|lâmpada|lampada|luminária|luminaria)\b)', 'a ', clause)
                if n:
                    changes.append(Change('à','a','general_light_article',99.0,'grammar'))
                    clause=new

            toks=list(re.finditer(r"[\wÀ-ÿ+-]+",clause,flags=re.UNICODE))
            if strong and toks:
                first=toks[0]; raw=first.group(0); f=fold(raw)
                if f not in ACTIONS:
                    canonical=None; score=None
                    # High-value Portuguese action families. Existing valid verbs are excluded above.
                    if f.startswith(('ac','as')) and light_context:
                        canonical='acende'; score=94.0
                    elif f.startswith('ap') and light_context:
                        canonical='apaga'; score=94.0
                    else:
                        action_cands=['liga','desliga'] + (['acende','apaga'] if light_context else [])
                        scored=[]
                        for cand in action_cands:
                            ch=fuzz.ratio(f,cand); ph=fuzz.ratio(phonetic(f),phonetic(cand))
                            sc=.55*ch+.45*ph
                            # Initial-letter family is useful only with strong target gating.
                            if (f.startswith('l') and cand=='liga') or (f.startswith('d') and cand=='desliga'):
                                sc+=18
                            scored.append((sc,cand))
                        scored.sort(reverse=True)
                        best_sc,best_cand=scored[0]
                        margin=best_sc-(scored[1][0] if len(scored)>1 else 0)
                        # One-letter D/L survives some clipped captures; otherwise require acoustic evidence.
                        if (len(f)==1 and f in {'d','l'}) or (best_sc>=57 and margin>=7):
                            canonical = 'desliga' if f=='d' else ('liga' if f=='l' else best_cand)
                            score=best_sc
                    if canonical:
                        clause=clause[:first.start()]+canonical+clause[first.end():]
                        changes.append(Change(raw,canonical,'general_action_family',round(score,1) if score is not None else None,'target_gated'))

            parts[pi]=clause
        return ''.join(parts),changes

    def cleanup_abandoned_action_fragments(self, text: str):
        """Drop a clearly abandoned action fragment immediately before a complete action.

        Example: `apa desliga o ar-condicionado` -> `desliga o ar-condicionado`.
        The fragment is removed only when it acoustically resembles an action and is directly
        followed by a valid action, so ordinary prefixes such as `agora desliga` survive.
        """
        changes=[]
        out=text
        pat=re.compile(r'(?<!\w)([\wÀ-ÿ+-]{2,6})\s+('+'|'.join(sorted((re.escape(a) for a in ACTIONS),key=len,reverse=True))+r')\b',re.I)
        pos=0
        while True:
            m=pat.search(out,pos)
            if not m: break
            frag=fold(m.group(1)); action=fold(m.group(2))
            if frag in ACTIONS:
                pos=m.end(); continue
            looks=False
            action_fragment_cands={'liga','ligue','ligar','desliga','desligue','desligar','acende','acenda','acender','apaga','apague','apagar','abre','abra','fecha','feche'}
            for cand in action_fragment_cands:
                if len(frag)>=2 and cand.startswith(frag):
                    looks=True; break
                if frag and cand and frag[0]==cand[0] and fuzz.ratio(frag,cand)>=70:
                    looks=True; break
            if not looks:
                pos=m.end(); continue
            before=m.group(0); repl=m.group(2)
            out=out[:m.start()]+repl+out[m.end():]
            changes.append(Change(before,repl,'abandoned_action_fragment',96.0,'grammar'))
            pos=max(0,m.start()-1)
        return out,changes

    def deduplicate_adjacent_targets(self, text: str):
        """Collapse immediate duplicate HA targets without scanning the whole inventory.

        The first span must already be an exact inventory phrase. The following span must be
        adjacent (no connector/action) and highly similar. This keeps the hot path O(tokens).
        """
        out=text; changes=[]
        connectors={'e','ou','mas','de','do','da','dos','das','com','para','em','no','na'}
        for _ in range(4):
            ms=list(re.finditer(r'[\wÀ-ÿ+-]+',out,flags=re.UNICODE))
            if len(ms)<2: break
            toks=[m.group(0) for m in ms]; ftoks=[fold(t) for t in toks]
            removed=False
            # Longest reasonable entity phrase first.
            for n in range(min(5,len(ms)//2),0,-1):
                for i in range(0,len(ms)-2*n+1):
                    g1=' '.join(toks[i:i+n]); f1=fold(g1)
                    exact=self._exact_index.get(f1)
                    if exact is None or not exact[0].entity_ids:
                        continue
                    if ftoks[i+n] in connectors or ftoks[i+n] in ACTIONS:
                        continue
                    g2=' '.join(toks[i+n:i+2*n]); f2=fold(g2)
                    ratio=fuzz.ratio(f1,f2); part=fuzz.partial_ratio(f1,f2)
                    if ratio<80 or part<84:
                        continue
                    if not words(g1) or not words(g2) or words(g1)[0][0:1]!=words(g2)[0][0:1]:
                        continue
                    rs=ms[i+n].start(); re_=ms[i+2*n-1].end()
                    before=out[rs:re_]
                    out=(out[:rs].rstrip()+' '+out[re_:].lstrip()).strip()
                    changes.append(Change(before,'','deduplicate_target',round(.6*ratio+.4*part,1),'inventory',list(exact[0].entity_ids)))
                    removed=True; break
                if removed: break
            if not removed: break
        return out,changes

    def final_owner_cleanup(self, text: str):
        """Repair clipped person names only when attached to a real person-owned HA target.

        Examples from real captures:
          "liga o ventilador <nome truncado>" -> alvo canônico do inventário
          "liga o ventilador de <fragmento>" -> alvo canônico do inventário
          "ligue o Ventilador de <pessoa> em" -> trailing filler removed

        The short fragments are never rewritten globally; the target must match an actual
        inventory entity whose canonical name ends in a known HA person.
        """
        changes=[]
        parts=re.split(r'([,;.!?]+)',text)
        fragment_map={fold(k):v for k,v in self.confusions.get('person_fragments',{}).items()}
        fragment_map.update({fold(k):v for k,v in self.confusions.get('words',{}).items() if fold(v) in {fold(p.get('name')) for p in self.inventory.get('persons',[]) if p.get('name')}})

        for pi in range(0,len(parts),2):
            clause=parts[pi]
            if not fold(clause):
                continue

            # If inventory/fuzzy already recovered the full canonical owner but ASR left a
            # trailing filler (observed "... de <pessoa> em"), strip only that terminal filler.
            for base,person,canonical,eid in self._owned_targets:
                pat=re.compile(r'(?i)\b'+re.escape(canonical)+r'\s+(?:em|hein|hem)\s*$')
                new,n=pat.subn(canonical,clause)
                if n:
                    changes.append(Change(canonical+' + filler',canonical,'owner_tail_cleanup',99.0,'inventory',[eid] if eid else None))
                    clause=new

            # Resolve a short/truncated owner token after a real owned target base.  Candidate
            # selection is across all actual owners of that same base, with a strict margin.
            by_base={}
            for base,person,canonical,eid in self._owned_targets:
                bucket=by_base.setdefault(fold(base),[])
                # Multiple HA entities can expose the same spoken target (e.g. fan + switch).
                # Candidate competition is by person/canonical name, not by entity row.
                if not any(fold(x[1])==fold(person) and fold(x[2])==fold(canonical) for x in bucket):
                    bucket.append((base,person,canonical,eid))

            for bf,cands in by_base.items():
                # Use the canonical base spelling; earlier lexical layers already normalize common
                # device misspellings such as ventulador -> ventilador.
                base=cands[0][0]
                m=re.search(r'(?i)\b'+re.escape(base)+r'\s+(?:(?:de|do|da|e)\s+)?([\wÀ-ÿ+-]{1,12})(?:\s+(?:em|hein|hem))?\s*$',clause)
                if not m:
                    continue
                raw_tail=m.group(1)
                ft=fold(raw_tail)
                if not ft:
                    continue
                scored=[]
                for _base,person,canonical,eid in cands:
                    pf=fold(person)
                    char=fuzz.ratio(ft,pf)
                    ph=fuzz.ratio(phonetic(ft),phonetic(pf)) if phonetic(ft) and phonetic(pf) else 0
                    sc=.6*char+.4*ph
                    if pf.startswith(ft) and len(ft)>=2:
                        sc+=12
                    if fragment_map.get(ft) and fold(fragment_map[ft])==pf:
                        sc=max(sc,96.0)
                    scored.append((sc,person,canonical,eid))
                scored.sort(reverse=True,key=lambda x:x[0])
                best=scored[0]
                second=scored[1][0] if len(scored)>1 else 0
                # Because the target itself is a real owner-specific HA entity, a low-ish acoustic
                # score is acceptable only with a wide winner margin.  This recovers Jie/já/Jaim
                # while avoiding arbitrary name substitutions.
                if best[0] < 50 or best[0]-second < 20:
                    continue
                start=m.start(); end=m.end()
                prefix=clause[:start]
                replacement=best[2]
                clause=prefix+replacement
                changes.append(Change(m.group(0).strip(),replacement,'owner_context_rescue',round(best[0],1),'inventory',[best[3]] if best[3] else None))
                break

            parts[pi]=clause
        return ''.join(parts),changes

    def normalize_action_tokens(self, text: str):
        """Repair damaged action words anywhere in a long command stream.

        Unlike generalized_command_rescue (which is strongest at clause starts), this pass can
        recover a later `desiga`/`leira` only when the following words contain a strong home
        target. It therefore remains inert in ordinary free text.
        """
        changes=[]; out=text
        action_cands=['liga','desliga','acende','apaga','ligue','desligue','acenda','apague']
        for _ in range(8):
            ms=list(re.finditer(r'[\wÀ-ÿ+-]+',out,flags=re.UNICODE))
            toks=[m.group(0) for m in ms]; ft=[fold(t) for t in toks]
            changed=False
            for i,m in enumerate(ms):
                f=ft[i]
                if not f or f in ACTIONS or len(f)<3 or f.startswith('lav'):
                    continue
                # Target evidence in the next few tokens; stop at an already-valid action.
                tail=[]
                for j in range(i+1,min(len(ms),i+6)):
                    if ft[j] in ACTIONS: break
                    tail.append(ft[j])
                if not tail: continue
                strong=False
                for tw in tail:
                    if tw in self.area_fold:
                        strong=True; break
                    for dev,_is_light in self._device_term_meta:
                        if (' ' not in dev and fuzz.ratio(tw,dev)>=86) or tw==dev:
                            strong=True; break
                    if strong: break
                if not strong:
                    continue
                scored=[]
                for cand in action_cands:
                    ch=fuzz.ratio(f,cand); ph=fuzz.ratio(phonetic(f),phonetic(cand))
                    sc=.55*ch+.45*ph
                    if f[0:1]==cand[0:1]: sc+=8
                    scored.append((sc,cand))
                scored.sort(reverse=True)
                best,second=scored[0],scored[1]
                if best[0] < 76 and not (f[0:1]==best[1][0:1] and best[0]>=68):
                    continue
                if best[0]-second[0] < 8:
                    continue
                before=m.group(0); after=best[1]
                out=out[:m.start()]+after+out[m.end():]
                changes.append(Change(before,after,'contextual_action_fuzzy',round(best[0],1),'target_gated'))
                changed=True; break
            if not changed: break
        return out,changes

    def fuzzy_only(self,text:str):
        toks=re.findall(r"[\wÀ-ÿ+-]+|[^\wÀ-ÿ]+",text,flags=re.UNICODE)
        changes=[]; out=[]
        folded_terms=[fold(x) for x in self.generic_terms]
        for tok in toks:
            ft=fold(tok)
            if not ft or not tok[0].isalnum() or len(ft)<4 or ft in STOPWORDS or ft in ACTIONS:
                out.append(tok); continue
            match=process.extractOne(ft,folded_terms,scorer=fuzz.ratio,score_cutoff=84)
            if not match:
                out.append(tok); continue
            _,score,idx=match; cand=self.generic_terms[idx]
            # Preserve number: fuzzy spelling cleanup must not silently turn plural device commands into singular.
            if ft.endswith('s') and not fold(cand).endswith('s'):
                out.append(tok); continue
            # Phonetic confirmation for medium-confidence replacements.
            ps=fuzz.ratio(phonetic(ft),phonetic(cand)) if phonetic(ft) and phonetic(cand) else 0
            if score>=91 or (score>=84 and ps>=82):
                if fold(cand)!=ft:
                    changes.append(Change(tok,cand,'generic_fuzzy',round((.7*score+.3*ps),1),'generic'))
                    out.append(cand)
                else: out.append(tok)
            else: out.append(tok)
        return ''.join(out),changes

    def inventory_only(self,text:str):
        """Resolve target spans after every recognized action, even in punctuation-free streams.

        Long microphone captures often arrive as `liga X desliga Y apaga Z` with no commas.
        Older phases only resolved the first action in each punctuation clause.  This version
        processes every action window from right to left so later replacements cannot shift
        earlier token offsets.
        """
        changes=[]
        parts=re.split(r'([,;.!?]+)',text)
        action_set=ACTIONS
        article={'a','o','as','os','um','uma'}
        query_actions={'qual','quanto','quanta','mostra','mostre','diz','diga'}

        def resolve_target(target:str):
            ft=fold(target); nt=len(words(target))
            if not ft: return None
            exact=self._exact_index.get(ft)
            if exact is not None:
                return 103.0, exact[0]
            pools=[]
            seen=set()
            for n in (nt,nt-1,nt+1,nt-2,nt+2):
                if n<=0: continue
                for item in self._candidate_index.get(n,()):
                    key=(item[0].canonical,item[0].source,item[0].entity_ids)
                    if key not in seen:
                        pools.append(item); seen.add(key)
            pht=phonetic(ft); tw=words(target); eligible=[]
            for e,ef,eph,en in pools:
                char=fuzz.ratio(ft,ef); part=fuzz.partial_ratio(ft,ef)
                if max(char,part)<66: continue
                if 'louc' in ft and fold(e.canonical)=='lava lamp':
                    continue
                ph=fuzz.ratio(pht,eph) if pht and eph else 0
                ew=words(e.canonical)
                sig_t=[w for w in tw if w not in STOPWORDS]
                sig_e=[w for w in ew if w not in STOPWORDS]
                if sig_t and sig_e:
                    sft=' '.join(sig_t); sfe=' '.join(sig_e)
                    char=max(char,fuzz.ratio(sft,sfe))
                    part=max(part,fuzz.partial_ratio(sft,sfe))
                tok_penalty=abs(len(sig_e or ew)-len(sig_t or tw))*4.0
                score=.55*char+.20*ph+.25*part+min(e.priority,3)*1.2-tok_penalty
                def _family(ws):
                    if not ws: return None
                    h=ws[0]
                    if h.startswith(('tv','tev','tvo')) or h in {'televisao'}: return 'tv'
                    if h in {'luz','lampada','luminaria','iluminacao','led','abajur','lustre','plafon'}: return 'light'
                    if h.startswith(('vent','filador','efilador','ilador')): return 'fan'
                    if h=='ar' or h.startswith('arcond') or h in {'climatizador','aquecedor'}: return 'climate'
                    if h in {'porta','janela','portao','fechadura','persiana','cortina'}: return 'opening'
                    return None
                tfam=_family(tw); efam=_family(ew)
                # If the broken target still carries a recognizable device family, never jump
                # across families (e.g. TV -> luz) just because the room/name suffix is similar.
                if tfam and efam and tfam!=efam:
                    continue
                if tfam and not efam:
                    continue
                # Owner/name tail: preserve first consonant as strong evidence only for `... de NAME`.
                if len(tw)>=3 and len(ew)>=3 and tw[:-1]==ew[:-1] and tw[-2]=='de' and ew[-2]=='de' and len(tw[-1])>=2:
                    if tw[-1][0]==ew[-1][0]: score+=12
                    else: score-=8
                threshold=92 if nt==1 else 82
                if e.source in {'person','entity_alias','device_alias','generated_domain_area','generated_media_area'}:
                    threshold-=2
                if score>=threshold:
                    eligible.append((score,e))
            if not eligible:
                return None
            eligible.sort(key=lambda x:x[0],reverse=True)
            top=eligible[0]; second=eligible[1][0] if len(eligible)>1 else 0
            if top[0]>=90 or top[0]-second>=4:
                return top
            return None

        for pi in range(0,len(parts),2):
            clause=parts[pi]
            matches=list(re.finditer(r"[\wÀ-ÿ+]+",clause,flags=re.UNICODE))
            if not matches: continue
            folded=[fold(m.group(0)) for m in matches]
            action_indices=[i for i,t in enumerate(folded) if t in action_set]
            if not action_indices: continue

            # Right-to-left keeps offsets valid for earlier commands in the same clause.
            for pos in range(len(action_indices)-1,-1,-1):
                ai=action_indices[pos]
                if folded[ai] in query_actions:
                    continue
                ti=ai+1
                while ti<len(matches) and folded[ti] in article:
                    ti+=1
                if ti>=len(matches): continue

                next_ai=action_indices[pos+1] if pos+1<len(action_indices) else len(matches)
                stop_at=next_ai
                # If the previous target is coordinated with a new explicit action, leave `e` outside it.
                if stop_at<len(matches) and stop_at-1>=ti and folded[stop_at-1]=='e':
                    stop_at-=1
                for j in range(ti,stop_at):
                    if folded[j] in {'para','em','com'} and j-ti>=1:
                        stop_at=j; break
                if stop_at<=ti: continue

                target_start=matches[ti].start(); target_end=matches[stop_at-1].end()
                target=clause[target_start:target_end].strip()
                result=resolve_target(target)
                if not result: continue
                score,e=result; ft=fold(target)
                if fold(e.canonical)==ft:
                    if e.entity_ids:
                        changes.append(Change(target,target,'inventory_hint',round(score,1),e.source,list(e.entity_ids)))
                    continue
                # Avoid semantic expansion by more than one lexical token.
                if len(words(e.canonical)) > len(words(target))+1:
                    continue
                before=target; after=e.canonical
                clause=clause[:target_start]+after+clause[target_end:]
                changes.append(Change(before,after,'inventory_context',round(score,1),e.source,list(e.entity_ids)))

            parts[pi]=clause
        return ''.join(parts),changes

    def apply(self,text:str,profile:str):
        start=time.perf_counter(); changes=[]; out=text
        if profile=='raw': pass
        elif profile=='normalize_only': out,changes=self.normalize_only(out)
        elif profile=='acoustic_only': out,changes=self.acoustic_only(out)
        elif profile=='fuzzy_only': out,changes=self.fuzzy_only(out)
        elif profile=='inventory_only': out,changes=self.inventory_only(out)
        elif profile=='full':
            for fn in (self.normalize_only,self.acoustic_only,self.generalized_command_rescue,self.command_rescue,self.cleanup_abandoned_action_fragments,self.fuzzy_only,self.normalize_action_tokens,self.inventory_only,self.final_owner_cleanup,self.deduplicate_adjacent_targets):
                out,ch=fn(out); changes.extend(ch)
            out=prettify_basic(out)
        else: raise ValueError('perfil desconhecido: '+profile)
        elapsed=(time.perf_counter()-start)*1000
        # entity hints: collect from inventory changes and exact canonical mentions
        hints=[]
        for c in changes:
            if c.entity_ids:
                for eid in c.entity_ids:
                    if eid and eid not in hints: hints.append(eid)
        return {'text':out.strip(),'correction_ms':round(elapsed,3),'changes':[asdict(c) for c in changes],'entity_hints':hints[:20]}

    def stats(self):
        return {
            'inventory_entities':len(self.inventory.get('entities',[])),
            'inventory_devices':len(self.inventory.get('devices',[])),
            'inventory_areas':len(self.inventory.get('areas',[])),
            'lexicon_entries':len(self.entries),
            'generic_names':len(self.common_names),
            'generic_device_types':len(self.device_types),
        }

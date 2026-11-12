"""Bounded, read-only HA Recorder adapter, version/schema guarded.

No SQL, credentials or self-HTTP. Only the reviewed 2026.11 helper signatures
are enabled; other versions return history_unavailable until reviewed.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from functools import partial
import inspect
import json
import math

from .queries import QueryError, now, number, decimal

_pending={}
MAX_POINTS=4096


def window(hass,period):
    end=now(hass);midnight=end.replace(hour=0,minute=0,second=0,microsecond=0)
    if period=='today':start=midnight
    elif period=='yesterday':start,end=midnight-timedelta(days=1),midnight
    elif period=='last_hour':start=end.astimezone(timezone.utc)-timedelta(hours=1)
    elif period=='last_24h':start=end.astimezone(timezone.utc)-timedelta(hours=24)
    elif period=='this_week':start=midnight-timedelta(days=midnight.weekday())
    else:raise QueryError('history_period')
    return start.astimezone(timezone.utc),end.astimezone(timezone.utc)


def _series(raw,entity,start,end,unit=None):
    if type(raw) is not dict or set(raw)!={entity} or type(raw[entity]) is not list or not 1<=len(raw[entity])<=MAX_POINTS:
        raise QueryError('insufficient_history')
    result=[]
    for state in raw[entity]:
        stamp=state.last_changed
        value=state.state
        if unit is not None and state.attributes.get('unit_of_measurement')!=unit:raise QueryError('history_units')
        if not isinstance(stamp,datetime) or stamp.tzinfo is None or type(value) is not str or len(value.encode())>256:
            raise QueryError('history_schema')
        result.append({'t':stamp.timestamp(),'value':value})
    result.sort(key=lambda x:x['t'])
    if len({p['t'] for p in result})!=len(result) or any(p['t']>end.timestamp()+1 for p in result):raise QueryError('history_schema')
    return result


def _fetch(hass,rows,start,end,energy,statistics=False):
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.history import state_changes_during_period
    from homeassistant.components.recorder.statistics import statistics_during_period, get_metadata
    if energy or statistics:
        ids={r['entity_id'] for r in rows}
        metadata=get_metadata(hass,statistic_ids=ids,statistic_type='sum' if energy else 'mean')
        if set(metadata)!=ids or any(pair[1].get('unit_of_measurement')!= ('kWh' if energy else rows[0]['attributes'].get('unit_of_measurement')) or pair[1].get('has_sum' if energy else 'has_mean') is not True for pair in metadata.values()):raise QueryError('history_units')
        return statistics_during_period(hass,start-timedelta(hours=1) if energy else start,end,ids,'hour',None,{'sum'} if energy else {'mean','min','max'})
    result={};count=0
    for row in rows:
        entity=row['entity_id']
        unit=row.get('attributes',{}).get('unit_of_measurement')
        series=[];cursor=start
        while cursor<end:
            stop=min(end,cursor+timedelta(hours=24))
            raw=state_changes_during_period(hass,cursor,stop,entity_id=entity,no_attributes=unit is None,limit=MAX_POINTS+1-count-len(series),include_start_time_state=True)
            part=_series(raw,entity,cursor,stop,unit)
            seen={p['t']:p['value'] for p in series}
            for p in part:
                if p['t'] in seen and seen[p['t']]!=p['value']:raise QueryError('history_schema')
                if p['t'] not in seen:series.append(p)
            if count+len(series)>=MAX_POINTS:raise QueryError('history_point_limit')
            cursor=stop
        count+=len(series)
        if count>=MAX_POINTS:raise QueryError('history_point_limit')
        result[entity]=series
    if len(json.dumps(result).encode())>1048576:raise QueryError('history_byte_limit')
    return result


async def fetch(hass,rows,start,end,energy,statistics=False):
    maximum=604800-3600 if energy else 604800 if statistics else 90000
    if not 1<=len(rows)<=8 or not 0<(end-start).total_seconds()<=maximum:raise QueryError('history_period_limit')
    try:
        from homeassistant.const import MAJOR_VERSION, MINOR_VERSION
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.history import state_changes_during_period
        if (MAJOR_VERSION,MINOR_VERSION)!=(2026,11) or not {'limit','include_start_time_state','no_attributes'}<=set(inspect.signature(state_changes_during_period).parameters):raise QueryError('history_unavailable')
        instance=get_instance(hass)
        if not instance.enabled or not instance.recording:raise QueryError('history_unavailable')
    except (ImportError,AttributeError,KeyError):raise QueryError('history_unavailable') from None
    key=id(hass)
    if key in _pending:raise QueryError('history_busy')
    task=asyncio.ensure_future(instance.async_add_executor_job(partial(_fetch,hass,rows,start,end,energy,statistics)))
    _pending[key]=task
    def done(future):
        if _pending.get(key) is future:_pending.pop(key,None)
        if not future.cancelled():future.exception()
    task.add_done_callback(done)
    try:
        async with asyncio.timeout(5):return await asyncio.shield(task)
    except TimeoutError:raise QueryError('history_timeout') from None
    except QueryError:raise
    except Exception:raise QueryError('history_unavailable') from None


def evaluate(series,params,start,end,label,unit):
    duration=(end-start).total_seconds();a=start.timestamp();b=end.timestamp()
    aggregate=params['aggregate']
    gap=300 if aggregate=='state_duration' else 900
    if len(series)<(2 if aggregate=='state_duration' else 3) or series[0]['t']>a+1 or series[-1]['t']<b-gap:
        raise QueryError('insufficient_history')
    if any(right['t']-max(left['t'],a)>gap for left,right in zip(series,series[1:])):
        raise QueryError('insufficient_history')
    if aggregate=='state_duration':
        states={'on','off','standby','idle','playing','paused','buffering'}
        if any(p['value'] not in states for p in series):raise QueryError('insufficient_history')
        total=sum(max(0,min(right['t'],b)-max(left['t'],a)) for left,right in zip(series,[*series[1:],{'t':b}]) if left['value'] not in ('off','standby'))
        return f'{label} ficou ligada por {decimal(total/60)} minutos no histórico observado.'
    try:values=[number(p['value']) for p in series]
    except QueryError:raise QueryError('insufficient_history') from None
    if params.get('metric')=='humidity' and any(not 0<=v<=100 for v in values):raise QueryError('insufficient_history')
    if params.get('metric')=='temperature' and any(v<{'°C':-273.15,'°F':-459.67,'K':0}[unit] for v in values):raise QueryError('insufficient_history')
    if aggregate=='peak':return f'O pico de {label} foi {decimal(max(values))} {unit} no período.'
    if aggregate!='trend':raise QueryError('history_aggregate')
    change=values[-1]-values[0]
    return f'{label}: {decimal(values[0])} → {decimal(values[-1])} {unit}; '+('subiu' if change>0 else 'caiu' if change<0 else 'permaneceu igual')+'.'


def energy_result(raw,rows,start,end):
    if type(raw) is not dict or set(raw)!={r['entity_id'] for r in rows}:raise QueryError('insufficient_history')
    answers=[]
    for row in rows:
        points=raw[row['entity_id']]
        if type(points) is not list or not 2<=len(points)<=170:raise QueryError('insufficient_history')
        if any(type(p) is not dict or type(p.get('start')) not in (int,float) or not math.isfinite(p['start']) or type(p.get('sum')) not in (int,float) or not math.isfinite(p['sum']) for p in points):raise QueryError('history_schema')
        points=sorted(points,key=lambda p:p['start'])
        # Hourly sum describes the end of its hour. Require exact boundaries;
        # partial hours and missing bins cannot silently become a full-day sum.
        if abs(points[0]['start']+3600-start.timestamp())>1 or abs(points[-1]['start']+3600-end.timestamp())>1:
            raise QueryError('insufficient_history')
        if any(abs(y['start']-x['start']-3600)>1 or y['sum']<x['sum'] for x,y in zip(points,points[1:])):raise QueryError('insufficient_history')
        answers.append(f"{row['name']} consumiu {decimal(points[-1]['sum']-points[0]['sum'])} kWh no período.")
    return ' '.join(answers)


def statistics_result(raw,row,params,start,end):
    if type(raw) is not dict or set(raw)!={row['entity_id']}:raise QueryError('insufficient_history')
    points=raw[row['entity_id']]
    if type(points) is not list or not 2<=len(points)<=169:raise QueryError('insufficient_history')
    for p in points:
        if type(p) is not dict or any(type(p.get(k)) not in (int,float) or not math.isfinite(p[k]) for k in ('start','end','mean','min','max')) or p['min']>p['mean'] or p['mean']>p['max']:
            raise QueryError('history_schema')
    points=sorted(points,key=lambda p:p['start'])
    if abs(points[0]['start']-start.timestamp())>1 or not 0<=end.timestamp()-points[-1]['end']<=3600:
        raise QueryError('insufficient_history')
    if any(abs(p['end']-p['start']-3600)>1 for p in points) or any(abs(a['end']-b['start'])>1 for a,b in zip(points,points[1:])):
        raise QueryError('insufficient_history')
    unit=row['attributes'].get('unit_of_measurement')
    stamp=datetime.fromtimestamp(points[-1]['end'],timezone.utc).isoformat()
    if params['aggregate']=='peak':
        return f"Pico registrado de {row['name']}: {decimal(max(p['max'] for p in points))} {unit}, com dados até {stamp}."
    if params['aggregate']!='trend':raise QueryError('history_aggregate')
    return f"A média horária de {row['name']} "+('subiu' if points[-1]['mean']>points[0]['mean'] else 'caiu' if points[-1]['mean']<points[0]['mean'] else 'permaneceu igual')+f", de {decimal(points[0]['mean'])} para {decimal(points[-1]['mean'])} {unit}, com dados até {stamp}."


async def query(hass,rows,params,authorize):
    start,end=window(hass,params.get('period'))
    energy=params.get('metric')=='energy'
    statistics=params.get('period')=='this_week' and not energy and params.get('metric')!='power_state'
    if len(rows)!=1:raise QueryError('ambiguous_source')
    await authorize()
    raw=await fetch(hass,rows,start,end,energy,statistics=statistics)
    await authorize()
    if energy:
        state=hass.states.get(rows[0]['entity_id'])
        if state.attributes.get('unit_of_measurement')!='kWh' or state.attributes.get('state_class') not in ('total','total_increasing'):raise QueryError('history_units')
        return energy_result(raw,rows,start,end)
    row=rows[0];state=hass.states.get(row['entity_id']);unit=state.attributes.get('unit_of_measurement','')
    allowed={'temperature':{'°C','°F','K'},'humidity':{'%'},'power':{'W'},'power_state':{''}}
    if unit not in allowed.get(params.get('metric'),set()):raise QueryError('history_units')
    if statistics:return statistics_result(raw,row,params,start,end)
    return evaluate(raw[row['entity_id']],params,start,end,row['name'],unit)

"""Bounded Recorder API fixtures; no residential history or SQL."""
import asyncio
from datetime import datetime,timedelta,timezone
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch,AsyncMock
from zoneinfo import ZoneInfo

ROOT=Path(__file__).parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'arandu_stack/rootfs/modules/arandu_nlu/rootfs/src/tests/mlp')]
from test_contextual import hass_
from custom_components.local_nlu import advanced_queries as history
from custom_components.local_nlu.queries import QueryError,render


class History(unittest.IsolatedAsyncioTestCase):
    def test_local_yesterday_and_last_24_hours_handle_dst(self):
        hass=hass_()
        for month,day,hours in [(3,9,23),(11,2,25)]:
            value=datetime(2026,month,day,12,tzinfo=ZoneInfo('America/New_York'))
            with patch.object(history,'now',return_value=value):
                a,b=history.window(hass,'yesterday')
                self.assertEqual((b-a).total_seconds(),hours*3600)
                a,b=history.window(hass,'last_24h')
                self.assertEqual((b-a).total_seconds(),86400)

    def test_trend_peak_and_insufficient_coverage(self):
        a=datetime(2026,11,12,tzinfo=timezone.utc);b=a+timedelta(minutes=30)
        series=[{'t':(a+timedelta(minutes=i*10)).timestamp(),'value':str(70-i)} for i in range(4)]
        self.assertIn('caiu',history.evaluate(series,{'aggregate':'trend'},a,b,'Umidade','%'))
        self.assertIn('70',history.evaluate(series,{'aggregate':'peak'},a,b,'Umidade','%'))
        for bad in (series[1:],series[:-2],[series[0],series[-1]],[{**p,'value':'unknown'} for p in series]):
            with self.assertRaises(QueryError):history.evaluate(bad,{'aggregate':'trend'},a,b,'Umidade','%')

    def test_binary_duration_requires_full_bounded_series(self):
        a=datetime(2026,11,12,tzinfo=timezone.utc);b=a+timedelta(minutes=20)
        series=[{'t':(a+timedelta(minutes=i*5)).timestamp(),'value':'on' if i<2 else 'off'} for i in range(5)]
        self.assertIn('10 minutos',history.evaluate(series,{'aggregate':'state_duration'},a,b,'TV',''))
        with self.assertRaises(QueryError):history.evaluate([series[0],series[-1]],{'aggregate':'state_duration'},a,b,'TV','')
        series[2]['value']='unavailable'
        with self.assertRaises(QueryError):history.evaluate(series,{'aggregate':'state_duration'},a,b,'TV','')

    def test_energy_sum_uses_boundary_delta_and_rejects_resets_missing_bins(self):
        a=datetime(2026,11,11,tzinfo=timezone.utc);b=a+timedelta(days=1)
        rows=[{'entity_id':'sensor.meter','name':'Medidor'}]
        points=[{'start':(a+timedelta(hours=i-1)).timestamp(),'sum':float(100+i)} for i in range(25)]
        self.assertIn('24 kWh',history.energy_result({'sensor.meter':points},rows,a,b))
        for bad in (points[1:],points[:-1],points[:5]+points[6:]):
            with self.assertRaises(QueryError):history.energy_result({'sensor.meter':bad},rows,a,b)
        points[5]['sum']=0
        with self.assertRaises(QueryError):history.energy_result({'sensor.meter':points},rows,a,b)

    def test_query_units_and_rank_never_mix_power_energy(self):
        rows=[{'value':5,'unit':'W','entity_id':'sensor.b','label':'B','metric':'power'},
              {'value':10,'unit':'kWh','entity_id':'sensor.a','label':'A','metric':'power'}]
        with self.assertRaises(QueryError):render(rows,{'aggregate':'rank','order':'desc'}, {})

    def test_weekly_statistics_report_hourly_coverage_and_reject_gaps(self):
        a=datetime(2026,11,9,tzinfo=timezone.utc);b=a+timedelta(hours=3,minutes=20)
        row={'entity_id':'sensor.humidity','name':'Umidade','attributes':{'unit_of_measurement':'%'}}
        points=[{'start':(a+timedelta(hours=i)).timestamp(),'end':(a+timedelta(hours=i+1)).timestamp(),
                 'mean':60-i,'min':59-i,'max':61-i} for i in range(3)]
        raw={'sensor.humidity':points}
        self.assertIn('média horária',history.statistics_result(raw,row,{'aggregate':'trend'},a,b))
        self.assertIn('Pico registrado',history.statistics_result(raw,row,{'aggregate':'peak'},a,b))
        with self.assertRaises(QueryError):history.statistics_result({'sensor.humidity':points[1:]},row,{'aggregate':'trend'},a,b)

    async def test_history_revalidates_authority_after_io(self):
        hass=hass_();rows=[{'entity_id':'sensor.humidity','name':'Umidade'}]
        authorize=AsyncMock(side_effect=[None,QueryError('permission_denied')])
        with patch.object(history,'fetch',new=AsyncMock(return_value={})):
            with self.assertRaisesRegex(QueryError,'permission_denied'):
                await history.query(hass,rows,{'period':'last_hour','metric':'humidity'},authorize)
        self.assertEqual(authorize.await_count,2)

    async def test_cancelled_native_query_remains_single_flight_until_completion(self):
        hass=hass_();entered=asyncio.Event();release=asyncio.Event();calls=[]
        async def run(job):
            calls.append(job);entered.set();await release.wait();return {}
        instance=SimpleNamespace(enabled=True,recording=True,async_add_executor_job=run)
        recorder=SimpleNamespace(get_instance=lambda _:instance)
        def state_changes_during_period(hass,start,end,*,limit=None,include_start_time_state=True,no_attributes=False):pass
        module=SimpleNamespace(state_changes_during_period=state_changes_during_period)
        const=SimpleNamespace(MAJOR_VERSION=2026,MINOR_VERSION=11)
        a=datetime.now(timezone.utc)-timedelta(hours=1);b=datetime.now(timezone.utc)
        with patch.dict(sys.modules,{'homeassistant.components.recorder':recorder,
            'homeassistant.components.recorder.history':module,'homeassistant.const':const}):
            task=asyncio.create_task(history.fetch(hass,[{'entity_id':'sensor.fixture'}],a,b,False))
            await entered.wait();task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            with self.assertRaisesRegex(QueryError,'history_busy'):
                await history.fetch(hass,[{'entity_id':'sensor.fixture'}],a,b,False)
            self.assertEqual(len(calls),1)
            release.set();await asyncio.sleep(.01)
            self.assertNotIn(id(hass),history._pending)

    async def test_period_limit_refuses_raw_week_before_recorder_access(self):
        hass=hass_();a=datetime.now(timezone.utc);b=a+timedelta(days=7)
        with self.assertRaisesRegex(QueryError,'history_period_limit'):
            await history.fetch(hass,[{'entity_id':'sensor.fixture'}],a,b,False)

"""Read-only factory tool schemas and validation; JS is the single capacity model."""
from __future__ import annotations
import math
import re

STATION_IDS=['REC-01','MIX-01','QC-01','PACK-01']


def factory_schemas(schema):
    station={'type':'string','enum':STATION_IDS}
    return [
        schema('get_factory_status','Read synthetic production station capacities, FIFO workload and held Lot IDs. Independent capacity view, not real equipment telemetry.',{'station_id':station}),
        schema('compare_factory_downtime','Read-only copied FIFO capacity branches. Explicit user station, downtime and horizon only. Output is packing completion to warehouse, NOT shipment. No release, reinspection or original mutation.',{
            'station_id':station,'downtime_minutes':{'type':'number','minimum':0,'maximum':240},
            'horizon_minutes':{'type':'number','minimum':1,'maximum':240}},['station_id','downtime_minutes','horizon_minutes'])
    ]


def validate_values(station_id,downtime_minutes,horizon_minutes):
    if station_id not in STATION_IDS: raise ValueError('Unknown station ID')
    for name,value,low in [('downtime_minutes',downtime_minutes,0),('horizon_minutes',horizon_minutes,1)]:
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low<=value<=240:
            raise ValueError(name+' must be a finite number '+str(low)+'..240')


def request_values(question,inputs):
    """Only bind structured UI fields or explicit typed labels; never infer defaults."""
    names={'station_id':'stationId','downtime_minutes':'downtimeMinutes','horizon_minutes':'horizonMinutes'}
    supplied={name:inputs[key] for name,key in names.items() if key in inputs}
    stations=set(re.findall(r'(?<![A-Za-z0-9_-])(?:REC|MIX|QC|PACK)-01(?![A-Za-z0-9_-])',question,re.I))
    if len(stations)>1: raise ValueError('Conflicting requested stations')
    labelled={}
    if stations: labelled['station_id']=stations.pop().upper()
    number=r'(-?\d+(?:\.\d+)?)'
    for name,label in [('downtime_minutes',r'(?:중단(?:\s*시간)?|정지(?:\s*시간)?|downtime(?:[\s_]minutes?)?)'),('horizon_minutes',r'(?:비교(?:\s*시간)?|분석(?:\s*시간)?|관측(?:\s*시간)?|horizon(?:[\s_]minutes?)?)')]:
        values={float(v) for v in re.findall(label+r'\s*(?:[:=：]\s*)?'+number,question,re.I)}
        if len(values)>1: raise ValueError('Conflicting explicitly labelled '+name)
        if values: labelled[name]=values.pop()
    if any(name in labelled and labelled[name]!=value for name,value in supplied.items()):
        raise ValueError('Structured factory inputs conflict with explicit question labels')
    values={**labelled,**supplied}
    if set(values)!=set(names): raise ValueError('Explicit stationId, downtimeMinutes and horizonMinutes are required; no defaults')
    validate_values(**values)
    return values


def validate_request(question,inputs,args):
    if not isinstance(args,dict) or set(args)!={'station_id','downtime_minutes','horizon_minutes'}:
        raise ValueError('Factory comparison requires exactly station_id, downtime_minutes and horizon_minutes')
    validate_values(**args)
    if args!=request_values(question,inputs): raise ValueError('Factory tool values must match explicit user inputs')


class FactoryTools:
    def get_factory_status(self,station_id=None):
        if station_id is not None and station_id not in STATION_IDS: raise ValueError('Unknown station ID')
        result=self.bridge('factory_status')
        if station_id: result['stations']=[s for s in result['stations'] if s['id']==station_id]
        self.factory=result
        evidence=[]
        for station in result['stations']:
            evidence.append(self.ev(station['id']+' 합성 설비 기록',station,key='FACTORY-'+station['id']))
            for field,label in [('capacityKgPerMinute','가정 처리량 kg/min'),('queuedKg','대기 물량 kg'),('heldKg','보류 물량 kg'),('status','상태')]:
                evidence.append(self.ev(station['id']+' '+label,station[field],key=station['id']+'-'+field))
        return {**result,'evidence':evidence}

    def compare_factory_downtime(self,station_id,downtime_minutes,horizon_minutes):
        validate_values(station_id,downtime_minutes,horizon_minutes)
        self.factorySimulation=self.bridge('factory_simulate',stationId=station_id,downtimeMinutes=downtime_minutes,horizonMinutes=horizon_minutes)
        evidence=self.ev('합성 설비 중단 복제 분기 계산',self.factorySimulation,key='FACTORY-SIMULATION')
        return {**self.factorySimulation,'evidence':[evidence]}

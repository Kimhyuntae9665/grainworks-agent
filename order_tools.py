"""Read-only customer-order evidence. JS owns all allocation arithmetic."""
from __future__ import annotations
import re

AMOUNTS=['requestedKg','allocatedKg','unallocatedKg','shippedKg','readyKg','workInProgressKg','heldKg','shippedImpactKg','remainingKg','notReadyKg']


def order_schemas(schema):
    return [schema('get_order_status','Read exact synthetic sales order and lines, allocated Lots/assets, shipped/ready/WIP/held/unallocated kg. No allocation or execution. Optional order_id; never infer order from a Lot.',
        {'order_id':{'type':'string','description':'Exact saved SO ID requested by user. Omit only for all orders.'}})]


def requested_orders(question):
    # IDs are case-sensitive saved keys, so preserve the explicitly typed spelling.
    tokens=set(re.findall(r'(?<![A-Za-z0-9_-])SO-[A-Za-z0-9_-]+',question,re.I))
    return {token for token in tokens if not re.search(r'-L\d+$',token,re.I)}


def validate_order_request(question,args):
    if not isinstance(args,dict) or set(args)-{'order_id'}: raise ValueError('Order tool accepts only optional order_id')
    explicit=requested_orders(question)
    if explicit and args.get('order_id') not in explicit:
        raise ValueError('Read exactly the explicitly requested order_id: '+', '.join(sorted(explicit))+'; no inferred order or all-order substitute')


def compact_orders(result):
    """Full evidence stays in the API; small local model sees relevant quantities."""
    if not isinstance(result,dict) or 'error' in result: return result
    pick=lambda record,keys:{key:record[key] for key in keys if key in record}
    orders=[];dispositions={}
    for order in result.get('orders',[]):
        record=pick(order,['id','dueTick','overdue',*AMOUNTS,'linkedAssetIds'])
        record['lines']=[]
        allocations=[]
        for line in order['lines']:
            line_record=pick(line,['id','product',*AMOUNTS,'linkedAssetIds'])
            line_record['allocations']=[pick(a,['lotId','quantityKg','stage','status','shippedImpact','needsConfirmation','blockingReasons','alertIds']) for a in line['allocations']]
            allocations.extend(line_record['allocations'])
            record['lines'].append(line_record)
        # This is a projection of the model's actual allocation disposition, not
        # a second classification or a grouping from the all-linked Lot list.
        for status,field in [('held','heldLotIds'),('shipped','shippedLotIds'),('ready','readyLotIds'),('work_in_progress','workInProgressLotIds')]:
            record[field]=sorted({a['lotId'] for a in allocations if a['status']==status})
        record['shippedImpactLotIds']=sorted({a['lotId'] for a in allocations if a['stage']=='shipped' and a.get('shippedImpact')})
        dispositions.update({a['lotId']:{'stage':a['stage'],'allocationStatus':a['status']} for a in allocations})
        orders.append(record)
    alerts=[{**alert,**dispositions.get(alert['lotId'],{})} for alert in result.get('blockingAlerts',[])]
    return {'synthetic':True,'orders':orders,'totals':result.get('totals',{}),'blockingAlerts':alerts}


class OrderTools:
    def get_order_status(self,order_id=None):
        if order_id is not None and (not isinstance(order_id,str) or not 1<=len(order_id)<=80):
            raise ValueError('Exact order ID required')
        result=self.bridge('orders',**({'order_id':order_id} if order_id is not None else {}))
        if order_id is not None and not any(order['id']==order_id for order in result['orders']):
            raise ValueError('Unknown order ID; no order can be inferred from a Lot')
        evidence=[];lot_ids=set()
        for order in result['orders']:
            oid=order['id']
            evidence.append(self.ev(oid+' 합성 주문',order,key='ORDER-'+oid))
            for field in AMOUNTS:
                evidence.append(self.ev(oid+' '+field+' kg',order[field],key=oid+'-'+field))
            for line in order['lines']:
                lid=line['id'];lot_ids.update(line.get('linkedLotIds',[]))
                evidence.append(self.ev(oid+' / '+lid+' 주문행',line,key='ORDER-'+oid+'-'+lid))
                for field in AMOUNTS:
                    evidence.append(self.ev(oid+' / '+lid+' '+field+' kg',line[field],key=oid+'-'+lid+'-'+field))
                for allocation in line['allocations']:
                    lot=self.lot(allocation['lotId']);lot_ids.add(lot['id'])
                    evidence.append(self.ev(oid+' / '+lid+' / '+lot['id']+' 배정 kg',allocation['quantityKg'],lot['id'],allocation.get('assetId'),key=oid+'-'+lid+'-'+lot['id']+'-allocation'))
                    evidence.append(self.ev(lot['id']+' 주문 연계 현재 Lot',{'id':lot['id'],'stage':lot['stage'],'status':lot['status'],'quantity':lot['quantity']},lot['id'],key='ORDER-LOT-'+lot['id']))
        blocking=[{key:a[key] for key in ['id','lotId','title','details','blocking','shippedImpact'] if key in a} for a in self.state['alerts'] if a['lotId'] in lot_ids and not a['resolved'] and a.get('blocking')]
        for alert in blocking:
            label='주문 연계 기출하 영향 ' if alert.get('shippedImpact') else '주문 연계 현재 차단 '
            evidence.append(self.ev(label+alert['id'],alert,alert['lotId'],key=alert['id']))
        # Union by ID for multiple explicit order reads. Sum only quantities from
        # these observed order records; never mix them with plant-wide Lot totals.
        prior={order['id']:order for order in (self.orders or {}).get('orders',[])}
        prior.update({order['id']:order for order in result['orders']})
        self.orders={**result,'orders':list(prior.values()),'totals':{field:sum(order[field] for order in prior.values()) for field in AMOUNTS}}
        return {**result,'blockingAlerts':blocking,'evidence':evidence}

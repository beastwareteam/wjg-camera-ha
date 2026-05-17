import asyncio, sys
asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
sys.path.insert(0, r'custom_components\wjg_camera')
from xm_soap import XMSoapClient, ENDPOINT_PTZ, PROFILE_TOKEN

async def test():
    async with XMSoapClient() as c:
        # Rohe PTZ-Antwort anzeigen
        body = f'''<tptz:ContinuousMove>
  <tptz:ProfileToken>{PROFILE_TOKEN}</tptz:ProfileToken>
  <tptz:Velocity><tt:PanTilt x="0.40" y="0.00"/></tptz:Velocity>
</tptz:ContinuousMove>'''
        import xml.etree.ElementTree as ET
        raw = await c._post(ENDPOINT_PTZ, body)
        print('Raw response:', ET.tostring(raw).decode() if raw is not None else 'None')

asyncio.run(test())

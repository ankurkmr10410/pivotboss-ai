import inspect
from neo_api_client import NeoAPI

print('NeoAPI file:', NeoAPI.__module__)
print('Constructor signature:', inspect.signature(NeoAPI.__init__))

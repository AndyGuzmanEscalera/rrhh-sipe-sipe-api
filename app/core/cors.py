from fastapi.middleware.cors import CORSMiddleware


def configure_cors(app,settings):
    origins=[v.strip() for v in settings.cors_allow_origins.split(',') if v.strip()]
    if '*' in origins: raise ValueError('Explicit CORS origins required')
    if origins: app.add_middleware(CORSMiddleware,allow_origins=origins,allow_credentials=True,
        allow_methods=['GET','POST','PUT','PATCH','OPTIONS'],allow_headers=['Authorization','Content-Type'])

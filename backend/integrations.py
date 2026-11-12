"""
Registers the integrated modules on the existing Flask app and creates their
tables. Each step is isolated: if one module can't start (e.g. database down),
the rest of the website keeps working and the reason is logged.
"""


def init_integrations(app):
    import core
    steps = [('core', lambda: (core.run_schema(core.CORE_SCHEMA, core.CORE_INDEXES), core.register_core_routes(app)))]

    def mod(name, fn_name):
        def run():
            m = __import__(name)
            getattr(m, fn_name)(app)
        return run

    steps += [('accounts', mod('accounts', 'init_accounts')),
              ('documents', mod('documents', 'init_documents')),
              ('finance', mod('finance', 'init_finance')),
              ('predictor', mod('predictor', 'init_predictor')),
              ('assistant', mod('assistant', 'init_assistant')),
              ('automation', mod('automation', 'init_automation')),
              ('tasks', mod('tasks', 'init_tasks')),
              ('management', mod('management', 'init_management')),
              ('analytics', mod('analytics', 'init_analytics')),
              ('payments', mod('payments', 'init_payments'))]
    for name, fn in steps:
        try:
            fn()
            print(f'[integrations] {name} ready')
        except ModuleNotFoundError as e:
            print(f'[integrations] {name} not installed yet: {e}')
        except Exception as e:
            print(f'[integrations] {name} failed to start: {e}')

from functools import update_wrapper


class Formatter:

    def process(self, data: str):
        raise NotImplementedError


class _FormatterWrapper(Formatter):

    def __init__(self, function, *default_args, **default_kwargs):
        self.func = function
        self.default_args = default_args
        self.default_kwargs = default_kwargs

    def __call__(self, *args, **kwargs):
        return self.func(*args, **kwargs)

    def __get__(self, instance, owner):
        return self

    def process(self, data: str):
        return self.func(data, *self.default_args, **self.default_kwargs)


def formatter(*args, **kwargs):

    def decorator(func):

        wrapper = _FormatterWrapper(func, *args, **kwargs)
        return update_wrapper(wrapper, wrapped=func)

    return decorator


@formatter()
def blank_formatter(data):
    return data


def code_formatter(language: str = ''):
    form = Formatter()

    def do_format(data):
        return '```{0}\n{1}\n```'.format(language, data)

    form.process = do_format
    return form


def wrap_formatter(lwrap=None, rwrap=None, wrap=None):
    form = Formatter()

    def do_format(data):
        if lwrap is not None:
            data = lwrap + data
        if rwrap is not None:
            data = data + rwrap
        if wrap is not None:
            data = wrap + data + wrap
        return data
    form.process = do_format

    return form
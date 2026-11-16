from fastapi import HTTPException


def problem(code, message, status=503):
    return HTTPException(status, detail={'code': code, 'message': message})

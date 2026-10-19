"""Offline regressions for fork-local image context accounting."""

import base64
from copy import deepcopy
import random

from deeptutor.services.session.context_builder import (
    IMAGE_CONTEXT_TOKEN_ESTIMATE,
    _count_model_context_tokens,
)

RANDOM_IMAGE = (
    "data:image/png;base64," + base64.b64encode(random.Random(17).randbytes(340000)).decode()
)


def image(url=RANDOM_IMAGE):
    return {"type": "image_url", "image_url": {"url": url, "detail": "auto"}}


def test_image_encoding_size_does_not_change_budget():
    small = _count_model_context_tokens([image("data:image/png;base64,AA==")])
    large = _count_model_context_tokens([image()])

    assert small == large
    assert large >= IMAGE_CONTEXT_TOKEN_ESTIMATE


def test_each_image_occurrence_is_counted():
    one = _count_model_context_tokens([image()])
    two = _count_model_context_tokens([image(), image()])

    assert two - one >= IMAGE_CONTEXT_TOKEN_ESTIMATE


def test_remote_image_url_is_not_language_text():
    local = _count_model_context_tokens([image()])
    remote = _count_model_context_tokens([image("https://example.test/image.png")])

    assert local == remote


def test_responses_and_anthropic_images_have_nonzero_allowance():
    parts = [
        {"type": "input_image", "image_url": RANDOM_IMAGE},
        {"type": "input_image", "file_id": "file-test"},
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": RANDOM_IMAGE},
        },
    ]
    counted = _count_model_context_tokens(parts)

    assert counted >= 3 * IMAGE_CONTEXT_TOKEN_ESTIMATE
    assert counted < 3 * IMAGE_CONTEXT_TOKEN_ESTIMATE + 200


def test_encoding_in_user_text_is_still_text():
    text_rows = [{"role": "user", "content": RANDOM_IMAGE}]
    image_rows = [{"role": "user", "content": [image()]}]

    assert _count_model_context_tokens(text_rows) > _count_model_context_tokens(image_rows)


def test_never_mutates_images_or_saved_rows():
    rows = [{"role": "user", "content": [image(), {"type": "text", "text": "keep"}]}]
    original = deepcopy(rows)

    _count_model_context_tokens(rows)

    assert rows == original

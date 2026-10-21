// "... response" nodes: answer with the `response` object prepared by the previous node.
return [{ json: $input.first().json.response }];

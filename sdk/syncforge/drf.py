"""
SyncForge — Django REST Framework (DRF) Integration
===================================================

Provides mixins for DRF viewsets to automatically serve cached data.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("syncforge.drf")

try:
    from rest_framework.response import Response
    from rest_framework.settings import api_settings
    HAS_DRF = True
except ImportError:
    HAS_DRF = False

class CachedListModelMixin:
    """
    Mixin for DRF ViewSets to serve lists of objects from SyncForge cache.
    
    Usage::
    
        from rest_framework import viewsets
        from syncforge.drf import CachedListModelMixin
        from sf_client import sf
        
        class ProductViewSet(CachedListModelMixin, viewsets.ModelViewSet):
            queryset = Product.objects.all()
            serializer_class = ProductSerializer
            sf_client = sf
            sf_table = 'core_product'
            sf_cache_key = 'products_list'
            sf_timeout = 3600
    """
    sf_client = None
    sf_table = None
    sf_cache_key = None
    sf_timeout = 3600

    def list(self, request, *args, **kwargs):
        if not HAS_DRF:
            raise ImportError("Django REST Framework is not installed.")
            
        if not self.sf_client or not self.sf_table:
            raise ValueError(
                f"{self.__class__.__name__} must define 'sf_client' and 'sf_table'."
            )
            
        queryset = self.filter_queryset(self.get_queryset())
        
        # Build cache key based on query parameters if not static
        cache_key = self.sf_cache_key
        if not cache_key:
            # Generate a key based on the URL and query parameters to differentiate
            # pagination, sorting, and filters.
            query_string = request.META.get('QUERY_STRING', '')
            cache_key = f"{self.sf_table}_list_{query_string}"

        # Fetch from cache using SyncForge Engine
        data = self.sf_client.cache_query(
            table_name=self.sf_table,
            cache_key=cache_key,
            queryset=queryset,
            timeout=self.sf_timeout
        )
        
        page = self.paginate_queryset(data)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(data, many=True)
        return Response(serializer.data)

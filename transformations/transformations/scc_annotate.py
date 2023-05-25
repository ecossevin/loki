# (C) Copyright 2018- ECMWF.
# This software is licensed under the terms of the Apache Licence Version 2.0
# which can be obtained at http://www.apache.org/licenses/LICENSE-2.0.
# In applying this licence, ECMWF does not waive the privileges and immunities
# granted to it by virtue of its status as an intergovernmental organisation
# nor does it submit to any jurisdiction.

from loki.transform import resolve_associates
from loki.expression import symbols as sym
from loki import(
           Transformation, ir, Transformer, FindNodes, pragmas_attached,
           CaseInsensitiveDict, DerivedType, FindVariables, flatten, as_tuple,
           FindScopes
)
from transformations.scc_base import SCCBaseTransformation

__all__ = ['SCCAnnotateTransformation']

class SCCAnnotateTransformation(Transformation):
    """
    A set of utilities to insert offload directives. This includes both :any:`Loop` and
    :any:`Subroutine` level annotations.

    Parameters
    ----------
    horizontal : :any:`Dimension`
        :any:`Dimension` object describing the variable conventions used in code
        to define the horizontal data dimension and iteration space.
    vertical : :any:`Dimension`
        :any:`Dimension` object describing the variable conventions used in code
        to define the vertical dimension, as needed to decide array privatization.
    block_dim : :any:`Dimension`
        Optional ``Dimension`` object to define the blocking dimension
        to use for hoisted column arrays if hoisting is enabled.
    directive : string or None
        Directives flavour to use for parallelism annotations; either
        ``'openacc'`` or ``None``.
    hoist_column_arrays : bool
        Flag to trigger the more aggressive "column array hoisting"
        optimization.
    """

    def __init__(self, horizontal, vertical, directive, block_dim, hoist_column_arrays=False):
        self.horizontal = horizontal
        self.vertical = vertical
        self.directive = directive
        self.block_dim = block_dim
        self.hoist_column_arrays = hoist_column_arrays

        self._processed = {}

    @classmethod
    def kernel_annotate_vector_loops_openacc(cls, routine, horizontal, vertical):
        """
        Insert ``!$acc loop vector`` annotations around horizontal vector
        loops, including the necessary private variable declarations.

        Parameters
        ----------
        routine : :any:`Subroutine`
            The subroutine in the vector loops should be removed.
        horizontal: :any:`Dimension`
            The dimension object specifying the horizontal vector dimension
        vertical: :any:`Dimension`
            The dimension object specifying the vertical loop dimension
        """

        # Find any local arrays that need explicitly privatization
        argument_map = CaseInsensitiveDict({a.name: a for a in routine.arguments})
        private_arrays = [v for v in routine.variables if not v.name in argument_map]
        private_arrays = [v for v in private_arrays if isinstance(v, sym.Array)]
        private_arrays = [v for v in private_arrays if not any(vertical.size in d for d in v.shape)]
        private_arrays = [v for v in private_arrays if not any(horizontal.size in d for d in v.shape)]

        with pragmas_attached(routine, ir.Loop):
            mapper = {}
            for loop in FindNodes(ir.Loop).visit(routine.body):
                if loop.variable == horizontal.index:
                    # Construct pragma and wrap entire body in vector loop
                    private_arrs = ', '.join(v.name for v in private_arrays)
                    pragma = ()
                    private_clause = '' if not private_arrays else f' private({private_arrs})'
                    pragma = ir.Pragma(keyword='acc', content=f'loop vector{private_clause}')
                    mapper[loop] = loop.clone(pragma=(pragma,))

            routine.body = Transformer(mapper).visit(routine.body)

    @classmethod
    def kernel_annotate_sequential_loops_openacc(cls, routine, horizontal):
        """
        Insert ``!$acc loop seq`` annotations around all loops that
        are not horizontal vector loops.

        Parameters
        ----------
        routine : :any:`Subroutine`
            The subroutine in which to annotate sequential loops
        horizontal: :any:`Dimension`
            The dimension object specifying the horizontal vector dimension
        """
        with pragmas_attached(routine, ir.Loop):

            for loop in FindNodes(ir.Loop).visit(routine.body):
                # Skip loops explicitly marked with `!$loki/claw nodep`
                if loop.pragma and any('nodep' in p.content.lower() for p in as_tuple(loop.pragma)):
                    continue

                if loop.variable != horizontal.index:
                    # Perform pragma addition in place to avoid nested loop replacements
                    loop._update(pragma=(ir.Pragma(keyword='acc', content='loop seq'),))

    @classmethod
    def kernel_annotate_subroutine_present_openacc(cls, routine):
        """
        Insert ``!$acc data present`` annotations around the body of a subroutine.

        Parameters
        ----------
        routine : :any:`Subroutine`
            The subroutine to which annotations will be added
        """

        # Get the names of all array and derived type arguments
        args = [a for a in routine.arguments if isinstance(a, sym.Array)]
        args += [a for a in routine.arguments if isinstance(a.type.dtype, DerivedType)]
        argnames = [str(a.name) for a in args]

        routine.body.prepend(ir.Pragma(keyword='acc', content=f'data present({", ".join(argnames)})'))
        # Add comment to prevent false-attachment in case it is preceded by an "END DO" statement
        routine.body.append((ir.Comment(text=''), ir.Pragma(keyword='acc', content='end data')))

    @classmethod
    def insert_annotations(cls, routine, horizontal, vertical, hoist_column_arrays):

        # Mark all non-parallel loops as `!$acc loop seq`
        cls.kernel_annotate_sequential_loops_openacc(routine, horizontal)

        # Mark all parallel vector loops as `!$acc loop vector`
        cls.kernel_annotate_vector_loops_openacc(routine, horizontal, vertical)

        # Wrap the routine body in `!$acc data present` markers
        # to ensure device-resident data is used for array and struct arguments.
        cls.kernel_annotate_subroutine_present_openacc(routine)

        if hoist_column_arrays:
            # Mark routine as `!$acc routine seq` to make it device-callable
            routine.spec.append(ir.Pragma(keyword='acc', content='routine seq'))

        else:
            # Mark routine as `!$acc routine vector` to make it device-callable
            routine.spec.append(ir.Pragma(keyword='acc', content='routine vector'))

    def transform_subroutine(self, routine, **kwargs):
        """
        Apply SCCAnnotate utilities to a :any:`Subroutine`.

        Parameters
        ----------
        routine : :any:`Subroutine`
            Subroutine to apply this transformation to.
        role : string
            Role of the subroutine in the call tree; should be ``"kernel"``
        """

        # TODO: we only need this here until the scheduler can combine multiple transformations into single pass
        # Bail if routine has already been processed
        if self._processed.get(routine, None):
            return

        role = kwargs['role']
        targets = kwargs.get('targets', None)
        item = kwargs.get('item', None)

        if role == 'kernel':
            self.process_kernel(routine)
        if role == 'driver':
            self.process_driver(routine, targets=targets, item=item)

        # Mark routine as processed
        self._processed[routine] = True

    def process_kernel(self, routine):
        """
        Applies the SCCAnnotate utilities to a "kernel". This consists of inserting the relevant
        ``'openacc'`` annotations at the :any:`Loop` and :any:`Subroutine` level.

        Parameters
        ----------
        routine : :any:`Subroutine`
            Subroutine to apply this transformation to.
        """

        # Bail if routine is marked as sequential
        if SCCBaseTransformation.check_routine_pragmas(routine, self.directive):
            return

        if self.directive == 'openacc':
            self.insert_annotations(routine, self.horizontal, self.vertical,
                                    self.hoist_column_arrays)

        # Remove section wrappers
        section_mapper = {s: s.body for s in FindNodes(ir.Section).visit(routine.body) if s.label == 'vector_section'}
        if section_mapper:
            routine.body = Transformer(section_mapper).visit(routine.body)

    def process_driver(self, routine, targets=None, item=None):
        """
        Apply the relevant ``'openacc'`` annotations to the driver loop.

        Parameters
        ----------
        routine : :any:`Subroutine`
            Subroutine to apply this transformation to.
        targets : list or string
            List of subroutines that are to be considered as part of
            the transformation call tree.
        item : :any:`Item`
            Scheduler work item corresponding to routine.
        """

        column_locals = []
        # Insert device side allocations for hoisted column locals
        if self.hoist_column_arrays:
            # Get list of hoisted column locals
            if item:
                column_locals = item.trafo_data['SCCHoistTransformation'].get('column_locals', None)
            if self.directive == 'openacc':
                self.device_alloc_column_locals(routine, column_locals)

        with pragmas_attached(routine, ir.Loop, attach_pragma_post=True):
            for call in FindNodes(ir.CallStatement).visit(routine.body):
                if not call.name in targets:
                    continue

                # Find the driver loop by checking the call's heritage
                ancestors = flatten(FindScopes(call).visit(routine.body))
                loops = [a for a in ancestors if isinstance(a, ir.Loop)]
                if not loops:
                    # Skip if there are no driver loops
                    continue
                driver_loop = loops[0]
                kernel_loop = [l for l in loops if l.variable == self.horizontal.index]
                if kernel_loop:
                    kernel_loop = kernel_loop[0]

                assert not driver_loop == kernel_loop

                # Mark driver loop as "gang parallel".
                self.annotate_driver(self.directive, driver_loop, kernel_loop, self.block_dim, column_locals)

    @classmethod
    def device_alloc_column_locals(cls, routine, column_locals):
        """
        Add explicit OpenACC statements for creating device variables for hoisted column locals.

        Parameters
        ----------
        routine : :any:`Subroutine`
            Subroutine to apply this transformation to.
        column_locals : list
            List of column locals to be hoisted to driver layer
        """

        if column_locals:
            vnames = ', '.join(v.name for v in column_locals)
            pragma = ir.Pragma(keyword='acc', content=f'enter data create({vnames})')
            pragma_post = ir.Pragma(keyword='acc', content=f'exit data delete({vnames})')
            # Add comments around standalone pragmas to avoid false attachment
            routine.body.prepend((ir.Comment(''), pragma, ir.Comment('')))
            routine.body.append((ir.Comment(''), pragma_post, ir.Comment('')))

    @classmethod
    def annotate_driver(cls, directive, driver_loop, kernel_loop, block_dim, column_locals):
        """
        Annotate driver block loop with ``'openacc'`` pragmas, and add offload directives
        for hoisted column locals.

        Parameters
        ----------
        directive : string or None
            Directives flavour to use for parallelism annotations; either
            ``'openacc'`` or ``None``.
        driver_loop : :any:`Loop`
            Driver ``Loop`` to wrap in ``'opencc'`` pragmas.
        kernel_loop : :any:`Loop`
            Vector ``Loop`` to wrap in ``'opencc'`` pragmas if hoisting is enabled.
        block_dim : :any:`Dimension`
            Optional ``Dimension`` object to define the blocking dimension
            to use for hoisted column arrays if hoisting is enabled.
        column_locals : list
            List of column locals to be hoisted to driver layer
        """

        # Mark driver loop as "gang parallel".
        if directive == 'openacc':
            arrays = FindVariables(unique=True).visit(driver_loop)
            arrays = [v for v in arrays if isinstance(v, sym.Array)]
            arrays = [v for v in arrays if not v.type.intent]
            arrays = [v for v in arrays if not v.type.pointer]
            arrays = [v for v in arrays if not v.name in [c.name for c in column_locals]]

            # Filter out arrays that are explicitly allocated with block dimension
            sizes = block_dim.size_expressions
            arrays = [v for v in arrays if not any(d in sizes for d in as_tuple(v.shape))]
            private_arrays = ', '.join(set(v.name for v in arrays))
            private_clause = '' if not private_arrays else f' private({private_arrays})'

            # Annotate vector loops with OpenACC pragmas
            if kernel_loop:
                kernel_loop._update(pragma=ir.Pragma(keyword='acc', content='loop vector'))

            if driver_loop.pragma is None:
                p_content = f'parallel loop gang{private_clause}'
                driver_loop._update(pragma=(ir.Pragma(keyword='acc', content=p_content),))
                driver_loop._update(pragma_post=(ir.Pragma(keyword='acc', content='end parallel loop'),))

            # add acc parallel loop gang if the only existing pragma is acc data
            elif len(driver_loop.pragma) == 1:
                if (driver_loop.pragma[0].keyword == 'acc' and
                    driver_loop.pragma[0].content.lower().lstrip().startswith('data ')):
                    p_content = f'parallel loop gang{private_clause}'
                    driver_loop._update(pragma=(driver_loop.pragma[0], ir.Pragma(keyword='acc', content=p_content)))
                    driver_loop._update(pragma_post=(ir.Pragma(keyword='acc', content='end parallel loop'),
                                              driver_loop.pragma_post[0]))

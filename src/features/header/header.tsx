import React, { Component } from 'react'
import PropTypes from 'prop-types'
import { connect } from 'react-redux'

export class Header extends Component {

    render() {
        return (
            <div className="header_wrapper">
                <h3>HEADER</h3>
            </div>
        )
    }
}

const mapStateToProps = (state: any) => ({
    
})

const mapDispatchToProps = {
    
}

export default connect(mapStateToProps, mapDispatchToProps)(Header)
